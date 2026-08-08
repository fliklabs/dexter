"""Chunking a batch, generating its entry ids, and reading the answer back.

**Entry ids are generated here and never surface.** The API requires them unique within a request
and they mean nothing outside it, so asking a caller to invent them would be exporting an
implementation detail. What comes back instead is the caller's own index — which is why `collect`
takes the chunk's offset: an entry reported as `1` in the second chunk of ten is the caller's
message number eleven, and reporting it as one would name the wrong message.

**A batch is partially successful by design.** SQS answers 200 with the refused entries listed in
the body, so reading only the status code says nothing about them.
"""

from collections.abc import Sequence
from typing import Any

from ..models import BatchFailure, BatchSuccess, OutboundMessage

BATCH_SIZE = 10
"""How many entries one batch request may carry.

A hard service limit for all three batch operations, not a choice.
"""


def chunked[T](items: Sequence[T], /) -> list[Sequence[T]]:
    """`items` split into runs of at most `BATCH_SIZE`."""
    return [
        items[start : start + BATCH_SIZE] for start in range(0, len(items), BATCH_SIZE)
    ]


def send_entry(offset: int, message: OutboundMessage, /) -> dict[str, Any]:
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


def handle_entries(handles: Sequence[str], /) -> list[dict[str, Any]]:
    """`DeleteMessageBatch` entries, one per receipt handle."""
    return [
        {"Id": str(offset), "ReceiptHandle": handle}
        for offset, handle in enumerate(handles)
    ]


def visibility_entries(
    handles: Sequence[str], visibility_timeout_seconds: int, /
) -> list[dict[str, Any]]:
    """`ChangeMessageVisibilityBatch` entries, one per receipt handle."""
    return [
        {
            "Id": str(offset),
            "ReceiptHandle": handle,
            "VisibilityTimeout": visibility_timeout_seconds,
        }
        for offset, handle in enumerate(handles)
    ]


def collect(
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
