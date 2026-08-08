"""What goes onto a queue or a topic, and what comes back off one.

**Every collection field is a tuple, and here that is more than the usual caution.** A frozen
pydantic model is only shallowly frozen, so a `list` field would leave the model mutable and
silently unhashable — and an `OutboundMessage` is precisely the kind of value that ends up in a
set of things to retry, because that is what a partially failed batch hands back.
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator


class SmsType(StrEnum):
    """How a carrier should treat a text message."""

    TRANSACTIONAL = "TRANSACTIONAL"
    """A message the recipient is waiting for: a login code, a confirmation. Optimised for
    delivery reliability, and priced accordingly."""

    PROMOTIONAL = "PROMOTIONAL"
    """Marketing. Cheaper, deprioritised, and subject to carrier filtering that a one-time
    code cannot survive."""


def describe_sms_type(sms_type: SmsType, /) -> str:
    """Render an SMS type as the symbol a caller would type."""
    return f"SmsType.{sms_type.name}"


class MessageAttribute(BaseModel):
    """One named string travelling beside a message body.

    String-valued only. SNS subscription filter policies and SQS consumers both match on
    strings and on numeric strings, so the `{"DataType": ..., "StringValue": ...}` envelope the
    API wants is built inside this module and never appears on a signature here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str


class OutboundMessage(BaseModel):
    """One message in a batch send.

    A batch is the only reason this type exists — a single send takes its parts as arguments,
    because naming them at the call site reads better than constructing a value to pass once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str
    delay_seconds: int = 0
    attributes: tuple[MessageAttribute, ...] = ()
    group_id: str | None = None
    """The FIFO ordering group. Messages sharing one are delivered in order, and messages in
    different groups are not ordered against each other — which is what makes a FIFO queue
    parallel at all."""

    deduplication_id: str | None = None
    """The FIFO deduplication key. SQS discards a repeat of the same id within five minutes."""

    @classmethod
    def of(
        cls,
        body: str,
        /,
        *,
        attributes: Mapping[str, str] | None = None,
        delay_seconds: int = 0,
        group_id: str | None = None,
        deduplication_id: str | None = None,
    ) -> Self:
        """Build one from a plain mapping of attributes.

        The stored field is a tuple of `MessageAttribute` so the model stays hashable; a caller
        writing one out has a dictionary. This converts, so neither side has to compromise.
        """
        return cls(
            body=body,
            delay_seconds=delay_seconds,
            attributes=tuple(
                MessageAttribute(name=name, value=value)
                for name, value in (attributes or {}).items()
            ),
            group_id=group_id,
            deduplication_id=deduplication_id,
        )

    @field_validator("body")
    @classmethod
    def _check_body(cls, body: str) -> str:
        """Reject an empty body, which SQS refuses with a parameter error naming no entry."""
        if not body:
            raise ValueError("A message body must not be empty.")
        return body


class ReceivedMessage(BaseModel):
    """One message taken off a queue, and the handle needed to finish with it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    receipt_handle: str
    """The handle this *receive* produced. Deleting or extending the message needs it, and it
    is not the message id — a redelivery of the same message carries a different one."""

    body: str
    attributes: tuple[MessageAttribute, ...] = ()
    approximate_receive_count: int = 1
    """How many times this message has been delivered, including now.

    What a consumer reads to decide something is poison. Without it the only way to stop a
    message from looping forever is a redrive policy on the queue, which is infrastructure.
    """

    sent_at: datetime | None = None


class BatchSuccess(BaseModel):
    """One entry of a batch that the service accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    """Position in the sequence the caller passed in.

    The caller's index, not the batch entry id. Entry ids are generated inside this module
    because the API requires them unique per request and they mean nothing outside it — so
    handing one back would be handing back an implementation detail.
    """

    message_id: str | None = None
    """The service's identifier, where the operation produces one. A delete does not."""


class BatchFailure(BaseModel):
    """One entry of a batch that the service refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    code: str
    message: str
    sender_fault: bool
    """Whether the fault is the caller's.

    The one field that says whether retrying is pointless: `True` means the request was wrong
    and will be wrong again, `False` means the service failed and the entry can go back.
    """


class BatchResult(BaseModel):
    """What a batch operation did, entry by entry.

    **A batch is partially successful by design**, answering 200 with the refused entries listed
    in the body. Returning nothing would discard exactly the half a caller has to act on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    succeeded: tuple[BatchSuccess, ...] = ()
    failed: tuple[BatchFailure, ...] = ()
