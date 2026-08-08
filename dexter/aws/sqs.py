"""Putting work on a queue, taking it off, and finishing with it.

Four operations and their batch forms, plus the name-to-URL lookup that configuration usually
makes necessary.

**Long polling is on by default, at twenty seconds.** Short polling samples a subset of the
queue's servers, so it returns empty while messages are waiting, and a consumer looping on it
spends money on empty answers. The cost is the one thing to know before using this at scale:
every call here runs on `asyncio.to_thread`, whose executor holds about thirty-two slots, and a
twenty-second receive occupies one for the whole twenty seconds. A worker polling eight queues
with the default executor has committed a quarter of the process's capacity to waiting. dexter
drives no event loop and so sets no executor; an application doing this should set its own. See
`dexter/aws/AGENTS.md`.

**Batch entry ids are generated here and never surface.** The API requires them unique within a
request and they mean nothing outside it, so asking a caller to invent them would be exporting
an implementation detail. What comes back instead is the caller's own index, in a `BatchResult`
that names which entries succeeded and which were refused — because a batch answers 200 with the
failures listed in the body, and a caller who checks only the status code has been told nothing.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ._calling import call
from .errors import MessageTooLargeError
from .models import (
    BatchFailure,
    BatchResult,
    BatchSuccess,
    MessageAttribute,
    OutboundMessage,
    ReceivedMessage,
)
from .session import AwsSession

BATCH_SIZE = 10
"""How many entries one batch request may carry. A hard service limit for all three batch
operations, not a choice."""

MAX_MESSAGE_BYTES = 262144
"""256 KiB, the largest message SQS accepts — and the largest total for a whole batch."""

FIFO_SUFFIX = ".fifo"
"""What a FIFO queue's name ends with. The only way to tell from a URL that a message needs a
group id, which is why the guard below can exist at all."""


class SqsClient:
    """Sends, receives, deletes and defers messages on a queue."""

    __slots__ = ("_session", "_urls")

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients; the queue-URL cache starts empty."""
        self._session = session
        self._urls: dict[str, str] = {}

    async def queue_url(self, queue_name: str) -> str:
        """The URL of the queue called `queue_name`, looked up once and kept.

        Configuration usually carries a name rather than a URL, so without this every consumer
        writes the same wrapper. Cached with no lifetime, deliberately: a queue's URL is derived
        from its name and the account, and it does not change while the queue exists. A queue
        deleted and recreated under the same name has the same URL again.

        Raises:
            ResourceNotFoundError: If there is no such queue.
            AwsRequestError: If the lookup was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        cached = self._urls.get(queue_name)
        if cached is not None:
            return cached

        response = await call(
            f"GetQueueUrl {queue_name}",
            lambda: self._session.sqs.get_queue_url(QueueName=queue_name),
        )
        url = response["QueueUrl"]
        self._urls[queue_name] = url
        return url

    async def send_message(  # noqa: PLR0913 - as `SnsClient.publish`, plus the delay
        self,
        queue_url: str,
        body: str,
        *,
        delay_seconds: int = 0,
        attributes: Mapping[str, str] | None = None,
        group_id: str | None = None,
        deduplication_id: str | None = None,
    ) -> str:
        """Put one message on the queue and return the identifier SQS gave it.

        Args:
            queue_url: The queue's URL. `queue_url(name)` resolves one from a name.
            body: The message. Serialising a structure into it is the caller's business.
            delay_seconds: How long SQS should hide the message before delivering it. Ignored
                by FIFO queues, which take a delay on the queue rather than the message.
            attributes: Named strings travelling beside the body.
            group_id: The FIFO ordering group. Required on a FIFO queue.
            deduplication_id: The FIFO deduplication key. Not required when the queue has
                content-based deduplication switched on.

        Returns:
            SQS's message identifier.

        Raises:
            ValueError: If the queue is FIFO and no `group_id` was given.
            MessageTooLargeError: If the message exceeds 256 KiB.
            ResourceNotFoundError: If the queue does not exist.
            AwsRequestError: If the send was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        _check_size(body)
        _check_fifo(queue_url, group_id)

        request: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MessageBody": body,
            "DelaySeconds": delay_seconds,
        }
        if attributes:
            request["MessageAttributes"] = _attributes(attributes)
        if group_id is not None:
            request["MessageGroupId"] = group_id
        if deduplication_id is not None:
            request["MessageDeduplicationId"] = deduplication_id

        response = await call(
            f"SendMessage to {queue_url}",
            lambda: self._session.sqs.send_message(**request),
        )
        return response["MessageId"]

    async def send_messages(
        self, queue_url: str, messages: Sequence[OutboundMessage]
    ) -> BatchResult:
        """Put many messages on the queue, ten at a time.

        Returns:
            Which entries were accepted and which were refused, by the caller's own index.

        Raises:
            ValueError: If the queue is FIFO and any message has no `group_id`.
            MessageTooLargeError: If any message, or any batch of ten, exceeds 256 KiB.
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        for message in messages:
            _check_size(message.body)
            _check_fifo(queue_url, message.group_id)

        succeeded: list[BatchSuccess] = []
        failed: list[BatchFailure] = []
        for start in range(0, len(messages), BATCH_SIZE):
            chunk = messages[start : start + BATCH_SIZE]
            _check_batch_size(chunk)
            entries = [_entry(offset, message) for offset, message in enumerate(chunk)]
            _collect(
                await self._send_batch(queue_url, entries), start, succeeded, failed
            )
        return BatchResult(succeeded=tuple(succeeded), failed=tuple(failed))

    async def receive_messages(
        self,
        queue_url: str,
        *,
        max_messages: int = 10,
        wait_seconds: int = 20,
        visibility_timeout_seconds: int | None = None,
    ) -> tuple[ReceivedMessage, ...]:
        """Take up to `max_messages` off the queue.

        Args:
            queue_url: The queue's URL.
            max_messages: How many to ask for. Ten is the service's maximum, and asking for ten
                does not mean ten arrive — a short answer is normal and is not the end of
                anything.
            wait_seconds: How long to hold the request open waiting for a message. Twenty is
                the maximum and the default; see the module docstring for what it costs.
            visibility_timeout_seconds: How long the returned messages stay hidden from other
                consumers. `None` uses the queue's own setting, which is the right answer unless
                this consumer is unusually slow.

        Returns:
            The messages, possibly none. Empty is an ordinary answer to a poll and never an
            error.

        Raises:
            ResourceNotFoundError: If the queue does not exist.
            AwsRequestError: If the receive was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MaxNumberOfMessages": max_messages,
            "WaitTimeSeconds": wait_seconds,
            # Asked for explicitly, because SQS returns neither by default and both are how a
            # consumer decides what to do: the attributes carry the receive count that identifies
            # a poison message, and the message attributes are the caller's own metadata.
            "MessageSystemAttributeNames": ["All"],
            "MessageAttributeNames": ["All"],
        }
        if visibility_timeout_seconds is not None:
            request["VisibilityTimeout"] = visibility_timeout_seconds

        response = await call(
            f"ReceiveMessage from {queue_url}",
            lambda: self._session.sqs.receive_message(**request),
        )
        return tuple(_received(raw) for raw in response.get("Messages", []))

    async def delete_message(self, queue_url: str, receipt_handle: str) -> None:
        """Finish with one message, so it is not delivered again.

        Raises:
            AwsRequestError: If the delete was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await call(
            f"DeleteMessage from {queue_url}",
            lambda: self._session.sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=receipt_handle
            ),
        )

    async def delete_messages(
        self, queue_url: str, receipt_handles: Sequence[str]
    ) -> BatchResult:
        """Finish with many messages, ten at a time.

        Returns:
            Which handles were accepted and which were refused, by the caller's own index. A
            refused delete matters more than it looks: that message will be delivered again.

        Raises:
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        succeeded: list[BatchSuccess] = []
        failed: list[BatchFailure] = []
        for start in range(0, len(receipt_handles), BATCH_SIZE):
            chunk = receipt_handles[start : start + BATCH_SIZE]
            entries = [
                {"Id": str(offset), "ReceiptHandle": handle}
                for offset, handle in enumerate(chunk)
            ]
            _collect(
                await self._delete_batch(queue_url, entries), start, succeeded, failed
            )
        return BatchResult(succeeded=tuple(succeeded), failed=tuple(failed))

    async def change_message_visibility(
        self, queue_url: str, receipt_handle: str, *, visibility_timeout_seconds: int
    ) -> None:
        """Give one message more time, or hand it back early.

        A handler that is going to take longer than the queue's visibility timeout extends it;
        one that has decided it cannot proceed sets it to zero, which returns the message
        immediately rather than making the next consumer wait the timeout out.

        Raises:
            AwsRequestError: If the change was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await call(
            f"ChangeMessageVisibility on {queue_url}",
            lambda: self._session.sqs.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=visibility_timeout_seconds,
            ),
        )

    async def change_messages_visibility(
        self,
        queue_url: str,
        receipt_handles: Sequence[str],
        *,
        visibility_timeout_seconds: int,
    ) -> BatchResult:
        """Change the visibility of many messages, ten at a time.

        Raises:
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        succeeded: list[BatchSuccess] = []
        failed: list[BatchFailure] = []
        for start in range(0, len(receipt_handles), BATCH_SIZE):
            chunk = receipt_handles[start : start + BATCH_SIZE]
            entries = [
                {
                    "Id": str(offset),
                    "ReceiptHandle": handle,
                    "VisibilityTimeout": visibility_timeout_seconds,
                }
                for offset, handle in enumerate(chunk)
            ]
            _collect(
                await self._visibility_batch(queue_url, entries),
                start,
                succeeded,
                failed,
            )
        return BatchResult(succeeded=tuple(succeeded), failed=tuple(failed))

    # Each batch request is its own method rather than a lambda built inside the loop above.
    # A closure over a loop variable is what ruff's B023 exists to catch — it is only safe here
    # because the call is awaited in the same iteration, and "safe for a reason that is one edit
    # away from being false" is not worth a suppression.

    async def _send_batch(
        self, queue_url: str, entries: list[dict[str, Any]], /
    ) -> Any:
        """Send one chunk of at most ten messages."""
        return await call(
            f"SendMessageBatch to {queue_url}",
            lambda: self._session.sqs.send_message_batch(
                QueueUrl=queue_url,
                Entries=entries,  # type: ignore[arg-type]
            ),
        )

    async def _delete_batch(
        self, queue_url: str, entries: list[dict[str, Any]], /
    ) -> Any:
        """Delete one chunk of at most ten messages."""
        return await call(
            f"DeleteMessageBatch from {queue_url}",
            lambda: self._session.sqs.delete_message_batch(
                QueueUrl=queue_url,
                Entries=entries,  # type: ignore[arg-type]
            ),
        )

    async def _visibility_batch(
        self, queue_url: str, entries: list[dict[str, Any]], /
    ) -> Any:
        """Change the visibility of one chunk of at most ten messages."""
        return await call(
            f"ChangeMessageVisibilityBatch on {queue_url}",
            lambda: self._session.sqs.change_message_visibility_batch(
                QueueUrl=queue_url,
                Entries=entries,  # type: ignore[arg-type]
            ),
        )


def _entry(offset: int, message: OutboundMessage, /) -> dict[str, Any]:
    """One `SendMessageBatch` entry, with its id generated from its position."""
    entry: dict[str, Any] = {
        "Id": str(offset),
        "MessageBody": message.body,
        "DelaySeconds": message.delay_seconds,
    }
    if message.attributes:
        entry["MessageAttributes"] = {
            attribute.name: {"DataType": "String", "StringValue": attribute.value}
            for attribute in message.attributes
        }
    if message.group_id is not None:
        entry["MessageGroupId"] = message.group_id
    if message.deduplication_id is not None:
        entry["MessageDeduplicationId"] = message.deduplication_id
    return entry


def _collect(
    response: Any,
    start: int,
    succeeded: list[BatchSuccess],
    failed: list[BatchFailure],
    /,
) -> None:
    """Read one batch answer, translating entry ids back into the caller's indexes.

    `start` is the offset of this chunk in the caller's sequence, so the index handed back is a
    position in what they passed rather than a position in a chunk they never saw.
    """
    succeeded.extend(
        BatchSuccess(index=start + int(entry["Id"]), message_id=entry.get("MessageId"))
        for entry in response.get("Successful", [])
    )
    failed.extend(
        BatchFailure(
            index=start + int(entry["Id"]),
            code=entry.get("Code", ""),
            message=entry.get("Message", ""),
            sender_fault=bool(entry.get("SenderFault", False)),
        )
        for entry in response.get("Failed", [])
    )


def _received(raw: Any, /) -> ReceivedMessage:
    """Turn one raw message into the shape this module hands back."""
    system = raw.get("Attributes", {})
    sent = system.get("SentTimestamp")
    attributes = tuple(
        MessageAttribute(name=name, value=value.get("StringValue", ""))
        for name, value in sorted(raw.get("MessageAttributes", {}).items())
    )
    return ReceivedMessage(
        message_id=raw.get("MessageId", ""),
        receipt_handle=raw.get("ReceiptHandle", ""),
        body=raw.get("Body", ""),
        attributes=attributes,
        approximate_receive_count=int(system.get("ApproximateReceiveCount", 1)),
        # SQS reports it as milliseconds since the epoch, in a string.
        sent_at=_timestamp(sent),
    )


def _timestamp(value: str | None, /) -> datetime | None:
    """Turn SQS's millisecond epoch string into a datetime, or `None` if it said nothing.

    Always tz-aware and always UTC. A naive datetime here would be one the caller has to guess
    the zone of, and the answer would be wrong on any machine that is not on UTC.
    """
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _attributes(attributes: Mapping[str, str], /) -> dict[str, Any]:
    """Wrap plain names and values in the envelope SQS wants."""
    return {
        name: {"DataType": "String", "StringValue": value}
        for name, value in attributes.items()
    }


def _check_size(body: str, /) -> None:
    """Refuse an oversized message before it is sent."""
    size = len(body.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The message is {size} bytes, and SQS accepts at most {MAX_MESSAGE_BYTES}."
        )


def _check_batch_size(messages: Sequence[OutboundMessage], /) -> None:
    """Refuse a batch whose total exceeds what one request may carry.

    The limit is on the whole request, not on each entry, so ten messages that are individually
    legal can still be refused together — with an error naming none of them.
    """
    total = sum(len(message.body.encode("utf-8")) for message in messages)
    if total > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The batch is {total} bytes, and SQS accepts at most {MAX_MESSAGE_BYTES} "
            f"across one request."
        )


def _check_fifo(queue_url: str, group_id: str | None, /) -> None:
    """Refuse a FIFO send with no ordering group.

    SQS answers `MissingParameter`, which names the parameter but not the reason — and the
    reason is that this queue is FIFO, which is visible right here in its URL.
    """
    if queue_url.endswith(FIFO_SUFFIX) and group_id is None:
        raise ValueError(
            f"{queue_url} is a FIFO queue, so every message needs a group_id."
        )
