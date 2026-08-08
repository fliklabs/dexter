"""Putting work on a queue, taking it off, and finishing with it.

Four operations and their batch forms, plus the name-to-URL lookup that configuration usually
makes necessary. The chunking, the entry ids and the answer-reading are in `_batching.py`; the
translation and the guards are in `_messages.py`; what is left here is the operations.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .._calling import call
from ..models import (
    BatchFailure,
    BatchResult,
    BatchSuccess,
    OutboundMessage,
    ReceivedMessage,
)
from ..session import AwsSession
from ._batching import (
    BATCH_SIZE,
    chunked,
    collect,
    handle_entries,
    send_entry,
    visibility_entries,
)
from ._messages import attributes as wrap_attributes
from ._messages import check_batch_size, check_fifo, check_size, received


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
        check_size(body)
        check_fifo(queue_url, group_id)

        request: dict[str, Any] = {
            "QueueUrl": queue_url,
            "MessageBody": body,
            "DelaySeconds": delay_seconds,
        }
        if attributes:
            request["MessageAttributes"] = wrap_attributes(attributes)
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
            check_size(message.body)
            check_fifo(queue_url, message.group_id)

        succeeded: list[BatchSuccess] = []
        failed: list[BatchFailure] = []
        for start, chunk in enumerate(chunked(messages)):
            check_batch_size(tuple(chunk))
            entries = [
                send_entry(offset, message) for offset, message in enumerate(chunk)
            ]
            collect(
                await self._send_batch(queue_url, entries),
                start * BATCH_SIZE,
                succeeded,
                failed,
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
                the maximum and the default; see the package docstring for what it costs.
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
        return tuple(received(raw) for raw in response.get("Messages", []))

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
        for start, chunk in enumerate(chunked(receipt_handles)):
            collect(
                await self._delete_batch(queue_url, handle_entries(chunk)),
                start * BATCH_SIZE,
                succeeded,
                failed,
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
        for start, chunk in enumerate(chunked(receipt_handles)):
            collect(
                await self._visibility_batch(
                    queue_url, visibility_entries(chunk, visibility_timeout_seconds)
                ),
                start * BATCH_SIZE,
                succeeded,
                failed,
            )
        return BatchResult(succeeded=tuple(succeeded), failed=tuple(failed))

    # Each batch request is its own method rather than a lambda built inside the loops above.
    # A closure over a loop variable is what ruff's B023 exists to catch — it is only safe there
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
