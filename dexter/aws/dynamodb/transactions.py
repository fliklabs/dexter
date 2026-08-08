"""All of it or none of it.

Its own file because a transaction is not a batch with stricter rules — it fails differently, and
the difference is what a caller has to act on. A batch reports which entries did not happen; a
transaction reports *why the whole thing did not*, in a `CancellationReasons` array whose entries
line up with the items that were sent. `_failures.py` reads that array; this assembles the request
and enforces the limit.

**`ClientRequestToken` is accepted and never generated.** A token dexter invented would differ on
the caller's own retry, which is exactly the moment idempotency is supposed to help — so
generating one would be a lie about a guarantee.
"""

from collections.abc import Sequence
from typing import Any

from botocore.exceptions import ClientError

from .._calling import call
from ..models import Item, TransactGet, TransactWrite
from ..session import AwsSession
from ._failures import translate_cancellation
from ._items import deserialise
from ._requests import transact_entry, transact_get_entry

TRANSACTION_SIZE = 100
"""How many entries one transaction may carry. Raised from 25 by the service in 2022."""


async def transact_write(
    session: AwsSession,
    items: Sequence[TransactWrite],
    request_token: str | None = None,
) -> None:
    """Apply every write, or none of them.

    Raises:
        ValueError: If `items` is empty or longer than a hundred.
        ConditionFailedError: If any condition was false, naming the entry's index.
        TransactionConflictError: If another transaction touched the same item.
        AwsRequestError: If the transaction was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    _check_size(len(items), "A transaction")

    request: dict[str, Any] = {
        "TransactItems": [transact_entry(item) for item in items]
    }
    if request_token is not None:
        request["ClientRequestToken"] = request_token

    def write() -> Any:
        try:
            return session.dynamodb.transact_write_items(**request)
        except ClientError as error:
            translate_cancellation(error)
            raise

    await call("TransactWriteItems", write)


async def transact_get(
    session: AwsSession, items: Sequence[TransactGet]
) -> list[Item | None]:
    """Read several items as one consistent snapshot.

    Returns:
        One entry per request, in the same order, with `None` where the item was absent.

    Raises:
        ValueError: If `items` is empty or longer than a hundred.
        TransactionConflictError: If another transaction touched one of the items.
        AwsRequestError: If the read was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    _check_size(len(items), "A transactional read")

    entries = [transact_get_entry(item) for item in items]

    def read() -> Any:
        try:
            return session.dynamodb.transact_get_items(TransactItems=entries)  # type: ignore[arg-type]
        except ClientError as error:
            translate_cancellation(error)
            raise

    response = await call("TransactGetItems", read)
    return [
        deserialise(entry["Item"]) if entry.get("Item") else None
        for entry in response.get("Responses", [])
    ]


def _check_size(count: int, what: str, /) -> None:
    """Refuse an empty or oversized transaction before the request is built.

    Raises:
        ValueError: If `count` is zero or above the service's limit.
    """
    if not count:
        raise ValueError(f"{what} must contain at least one entry.")
    if count > TRANSACTION_SIZE:
        raise ValueError(
            f"{what} may hold at most {TRANSACTION_SIZE} entries, and this has {count}."
        )
