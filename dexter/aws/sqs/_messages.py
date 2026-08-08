"""Turning messages into what SQS takes, and what it returns into dexter's own shape.

The translation layer, kept apart from the operations so that `client.py` reads as a list of
things a caller can do. The guards live here too, because each is a condition under which no
request should be made at all.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..errors import MessageTooLargeError
from ..models import MessageAttribute, OutboundMessage, ReceivedMessage

MAX_MESSAGE_BYTES = 262144
"""256 KiB, the largest message SQS accepts — and the largest total for a whole batch."""

FIFO_SUFFIX = ".fifo"
"""What a FIFO queue's URL ends with.

The only way to tell from a URL that a message needs a group id, which is what makes the guard
below possible at all.
"""


def attributes(named: Mapping[str, str], /) -> dict[str, Any]:
    """Plain names and values, in the envelope SQS wants."""
    return {
        name: {"DataType": "String", "StringValue": value}
        for name, value in named.items()
    }


def received(raw: Any, /) -> ReceivedMessage:
    """One raw message as the shape this module hands back."""
    system = raw.get("Attributes", {})
    return ReceivedMessage(
        message_id=raw.get("MessageId", ""),
        receipt_handle=raw.get("ReceiptHandle", ""),
        body=raw.get("Body", ""),
        attributes=tuple(
            MessageAttribute(name=name, value=value.get("StringValue", ""))
            for name, value in sorted(raw.get("MessageAttributes", {}).items())
        ),
        approximate_receive_count=int(system.get("ApproximateReceiveCount", 1)),
        sent_at=timestamp(system.get("SentTimestamp")),
    )


def timestamp(value: str | None, /) -> datetime | None:
    """SQS's millisecond epoch string as a datetime, or `None` if it said nothing.

    Always tz-aware and always UTC. A naive datetime here would be one the caller has to guess
    the zone of, and the guess would be wrong on any machine that is not on UTC.
    """
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def check_size(body: str, /) -> None:
    """Refuse an oversized message before it is sent.

    Raises:
        MessageTooLargeError: If the message exceeds 256 KiB.
    """
    size = len(body.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The message is {size} bytes, and SQS accepts at most {MAX_MESSAGE_BYTES}."
        )


def check_batch_size(messages: tuple[OutboundMessage, ...], /) -> None:
    """Refuse a batch whose total exceeds what one request may carry.

    **The limit is on the whole request, not on each entry**, so ten individually legal messages
    can still be refused together — with a service error naming none of them.

    Raises:
        MessageTooLargeError: If the batch exceeds 256 KiB in total.
    """
    total = sum(len(message.body.encode("utf-8")) for message in messages)
    if total > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The batch is {total} bytes, and SQS accepts at most {MAX_MESSAGE_BYTES} "
            f"across one request."
        )


def check_fifo(queue_url: str, group_id: str | None, /) -> None:
    """Refuse a FIFO send with no ordering group.

    SQS answers `MissingParameter`, which names the parameter but not the reason — and the reason
    is that this queue is FIFO, which is visible right there in its URL.

    Raises:
        ValueError: If the queue is FIFO and `group_id` is `None`.
    """
    if queue_url.endswith(FIFO_SUFFIX) and group_id is None:
        raise ValueError(
            f"{queue_url} is a FIFO queue, so every message needs a group_id."
        )
