"""Chunking batch requests, and retrying what the service did not process.

**`UnprocessedItems` is the reason this file exists.** DynamoDB answers a batch write with HTTP
200 and a map of the entries it declined to handle — usually because the table was throttled.
A client that reads only the status code has lost those writes, silently, and it loses them
precisely when throughput is short, which is the moment losing them matters most. The same is
true of `UnprocessedKeys` on a batch read, where the symptom is a partial answer that looks
complete.

So every batch here loops: send, collect what came back, resend what did not, with exponential
backoff and jitter between rounds, bounded by `AwsConfig.max_attempts`. What is still unprocessed
after that becomes `BatchIncompleteError` — loud, and naming the count.

**Jitter is not decoration.** Without it, a batch that was throttled retries on exactly the
schedule as every other client that was throttled by the same event, which is how a service
recovers into a second thundering herd.
"""

import asyncio
import secrets
from collections.abc import Sequence
from typing import Any

from .._calling import call
from ..errors import BatchIncompleteError
from ..models import Item
from ..session import AwsSession
from ._items import deserialise

BASE_BACKOFF_SECONDS = 0.05
"""How long to wait before the first retry, doubling each round."""

MAX_BACKOFF_SECONDS = 2.0
"""The ceiling on one wait. Beyond this the caller's own timeout is the better control."""


def chunked[T](items: Sequence[T], size: int, /) -> list[Sequence[T]]:
    """`items` split into runs of at most `size`."""
    return [items[start : start + size] for start in range(0, len(items), size)]


async def backoff(round_number: int, /) -> None:
    """Wait a little longer after each unsuccessful round, with jitter.

    Full jitter — a random wait between zero and the ceiling — rather than a fixed one, because
    the point is to spread retries out, and every client waiting the same growing interval keeps
    them synchronised.

    `secrets.randbelow` rather than `random.random`: it needs no seeding and cannot be perturbed
    by an application that seeds the global random module for its own reasons.
    """
    ceiling = min(BASE_BACKOFF_SECONDS * (2**round_number), MAX_BACKOFF_SECONDS)
    await asyncio.sleep(ceiling * secrets.randbelow(1000) / 1000)


def count_unprocessed(unprocessed: dict[str, Any], /) -> int:
    """How many entries a `UnprocessedItems` or `UnprocessedKeys` map still holds."""
    total = 0
    for value in unprocessed.values():
        if isinstance(value, list):
            total += len(value)
        elif isinstance(value, dict):
            total += len(value.get("Keys", []))
    return total


WRITE_BATCH_SIZE = 25
"""How many entries one `BatchWriteItem` may carry. A hard service limit."""

READ_BATCH_SIZE = 100
"""How many keys one `BatchGetItem` may carry. A hard service limit."""


async def write_batch(session: AwsSession, entries: dict[str, Any], /) -> None:
    """Send one chunk of writes, resending whatever the service declined.

    Raises:
        BatchIncompleteError: If entries were still unprocessed after the retry budget.
    """
    pending = entries
    for round_number in range(session.config.max_attempts):
        if round_number:
            await backoff(round_number)
        response = await _send_write(session, pending)
        unprocessed = response.get("UnprocessedItems") or {}
        if not unprocessed:
            return
        pending = unprocessed

    raise BatchIncompleteError(
        f"BatchWriteItem left {count_unprocessed(pending)} entries unprocessed after "
        f"{session.config.max_attempts} attempts."
    )


async def read_batch(
    session: AwsSession, keys: dict[str, Any], found: dict[str, list[Item]], /
) -> None:
    """Send one chunk of reads, resending whatever the service declined.

    Collects into `found` rather than returning, because a caller reading several tables
    accumulates across chunks and merging return values would be the same loop written twice.

    Raises:
        BatchIncompleteError: If keys were still unprocessed after the retry budget.
    """
    pending = keys
    for round_number in range(session.config.max_attempts):
        if round_number:
            await backoff(round_number)
        response = await _send_read(session, pending)
        for table, items in response.get("Responses", {}).items():
            found.setdefault(table, []).extend(deserialise(item) for item in items)
        unprocessed = response.get("UnprocessedKeys") or {}
        if not unprocessed:
            return
        pending = unprocessed

    raise BatchIncompleteError(
        f"BatchGetItem left {count_unprocessed(pending)} keys unprocessed after "
        f"{session.config.max_attempts} attempts."
    )


async def _send_write(session: AwsSession, pending: dict[str, Any], /) -> Any:
    """One `BatchWriteItem` request.

    Its own function rather than a lambda built inside the retry loop, because a closure over a
    loop variable is what ruff's B023 exists to catch.
    """
    return await call(
        "BatchWriteItem",
        lambda: session.dynamodb.batch_write_item(RequestItems=pending),
    )


async def _send_read(session: AwsSession, pending: dict[str, Any], /) -> Any:
    """One `BatchGetItem` request, for the same reason."""
    return await call(
        "BatchGetItem",
        lambda: session.dynamodb.batch_get_item(RequestItems=pending),
    )
