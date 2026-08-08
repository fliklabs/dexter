"""The ten operations, and what each one answers.

Everything under them is somewhere else: `_requests.py` assembles what goes on the wire,
`_items.py` owns the type policy, `_expressions.py` compiles conditions, `_batching.py` chunks
and retries, `transactions.py` handles all-or-nothing, and `paging.py` walks a result set. This
file is the surface a consumer reads.

Three things run through all of it:

- **Items are ordinary Python values on the way in and out.** DynamoDB's `{"S": "..."}` wire form
  appears on no signature.
- **Absence is `None`, never an exception.** `get_item` answers `None`, `transact_get_items`
  answers a list with `None` in it, and a query that matches nothing yields nothing. There is no
  `ItemNotFoundError`, for the same reason `head_object` has none.
- **A conditional write that loses its race raises `ConditionFailedError`**, which is not an
  `AwsRequestError`: nothing about the request was wrong. That distinction is what makes
  optimistic concurrency usable — the caller re-reads and retries the operation rather than the
  request.

`query` and `scan` are **not** `async`: they build a request and return an `ItemStream` that
pages as it is consumed. The alternative — an `async def` returning a list — is the version that
silently stops at the first megabyte.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .._calling import call
from ..models import (
    Condition,
    Item,
    ItemKey,
    TransactGet,
    TransactWrite,
    WriteRequest,
)
from ..session import AwsSession
from . import _failures, _requests
from ._batching import (
    READ_BATCH_SIZE,
    WRITE_BATCH_SIZE,
    chunked,
    read_batch,
    write_batch,
)
from ._items import deserialise, serialise
from .paging import ItemStream
from .transactions import transact_get, transact_write


class DynamoDbClient:
    """A document store: items in, items out, and transactions over several."""

    __slots__ = ("_session",)

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients."""
        self._session = session

    async def get_item(
        self,
        table: str,
        key: ItemKey,
        *,
        consistent_read: bool = False,
        attributes: Sequence[str] | None = None,
    ) -> Item | None:
        """The item at `key`, or `None` if there is none.

        Args:
            table: The table to read.
            key: The partition key, and the sort key if the table has one.
            consistent_read: Whether to read the latest write rather than a possibly stale
                replica. Costs twice as much and is what a read-after-write needs.
            attributes: Only these attributes, rather than the whole item. Reduces the bytes
                read, which is what DynamoDB charges for.

        Raises:
            ResourceNotFoundError: If the table does not exist.
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request = _requests.get_request(
            table, key, consistent_read=consistent_read, attributes=attributes
        )
        response = await call(
            f"GetItem {table}",
            lambda: self._session.dynamodb.get_item(**request),
        )
        item = response.get("Item")
        return deserialise(item) if item else None

    async def put_item(
        self, table: str, item: Item, *, condition: Condition | None = None
    ) -> None:
        """Write `item`, replacing whatever shares its key.

        Args:
            table: The table to write to.
            item: The whole item, including its key.
            condition: What must be true of the *existing* item for the write to happen.
                `Attr("pk").not_exists()` makes this an insert; `Attr("version").equals(3)`
                makes it an optimistic update.

        Raises:
            ConditionFailedError: If `condition` was false.
            AwsRequestError: If the write was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request: dict[str, Any] = {"TableName": table, "Item": serialise(item)}
        _requests.apply_condition(request, condition)

        await call(f"PutItem {table}", lambda: _failures.put(self._session, request))

    async def update_item(  # noqa: PLR0913 - SET/REMOVE/ADD/DELETE are four distinct operations
        self,
        table: str,
        key: ItemKey,
        *,
        set_values: Mapping[str, Any] | None = None,
        remove: Sequence[str] = (),
        add: Mapping[str, Any] | None = None,
        delete: Mapping[str, Any] | None = None,
        condition: Condition | None = None,
        return_updated: bool = False,
    ) -> Item | None:
        """Change part of the item at `key`, creating it if it is not there.

        Args:
            table: The table to write to.
            key: Which item to change.
            set_values: Attributes to write.
            remove: Attributes to delete from the item.
            add: Numbers to increment, or members to add to a set. This is the atomic
                counter — `add={"views": 1}` is safe against any number of concurrent callers,
                where a read-modify-write is not.
            delete: Members to take out of a set.
            condition: What must be true for the change to happen.
            return_updated: Whether to answer with the item as it is *after* the change.

        Returns:
            The updated item when `return_updated` is set, otherwise `None`.

        Raises:
            ValueError: If nothing was asked to change.
            ConditionFailedError: If `condition` was false.
            AwsRequestError: If the write was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request = _requests.update_request(
            table,
            key,
            set_values=dict(set_values) if set_values else None,
            remove=tuple(remove),
            add=dict(add) if add else None,
            delete=dict(delete) if delete else None,
            condition=condition,
            return_updated=return_updated,
        )
        response = await call(
            f"UpdateItem {table}", lambda: _failures.update(self._session, request)
        )
        attributes = response.get("Attributes")
        return deserialise(attributes) if attributes else None

    async def delete_item(
        self, table: str, key: ItemKey, *, condition: Condition | None = None
    ) -> None:
        """Remove the item at `key`.

        **Deleting something that is not there succeeds**, which is DynamoDB's behaviour rather
        than a choice made here, and the useful one for a cleanup that may already have run.

        Raises:
            ConditionFailedError: If `condition` was false.
            AwsRequestError: If the delete was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request: dict[str, Any] = {"TableName": table, "Key": serialise(key)}
        _requests.apply_condition(request, condition)

        await call(
            f"DeleteItem {table}", lambda: _failures.delete(self._session, request)
        )

    def query(  # noqa: PLR0913 - index, filter, projection and direction are independent choices
        self,
        table: str,
        key_condition: Condition,
        *,
        index: str | None = None,
        filter: Condition | None = None,  # noqa: A002
        attributes: Sequence[str] | None = None,
        ascending: bool = True,
        consistent_read: bool = False,
        page_size: int = 100,
    ) -> ItemStream:
        """Items matching a key condition, as a stream that pages.

        Args:
            table: The table to read.
            key_condition: Built with `Key`. The partition key must be compared with equality;
                the sort key may use a range or a prefix.
            index: A secondary index to read instead of the table itself.
            filter: A further test, built with `Attr`. **Applied after the read and charged for
                anyway** — it reduces what comes back, never what is scanned, which is why a
                filter is not a substitute for an index.
            attributes: Only these attributes.
            ascending: Whether to walk the sort key upwards. `False` is how "the most recent
                first" is spelled.
            consistent_read: Whether to read the latest write. Not available on a global
                secondary index.
            page_size: How many items to ask for per request.
        """
        request = _requests.query_request(
            table,
            key_condition,
            index=index,
            condition=filter,
            attributes=attributes,
            ascending=ascending,
            consistent_read=consistent_read,
            page_size=page_size,
        )
        return ItemStream(self._session, table, "Query", request)

    def scan(  # noqa: PLR0913 - as `query`, plus the two halves of a parallel scan
        self,
        table: str,
        *,
        index: str | None = None,
        filter: Condition | None = None,  # noqa: A002
        attributes: Sequence[str] | None = None,
        segment: int | None = None,
        total_segments: int | None = None,
        page_size: int = 100,
    ) -> ItemStream:
        """Every item in the table, as a stream that pages.

        **Reads the whole table and is charged for the whole table**, filter or no filter. It is
        the right operation for a migration or an export and the wrong one for a request path;
        anything a user waits on wants a `query` against a key or an index.

        Args:
            table: The table to read.
            index: A secondary index to scan instead.
            filter: A test applied after reading, which reduces what returns and not what is
                read.
            attributes: Only these attributes.
            segment: Which segment this worker is reading, for a parallel scan.
            total_segments: How many segments the scan is divided into. Both are needed
                together; one alone is refused by the service.
            page_size: How many items to ask for per request.
        """
        request = _requests.scan_request(
            table,
            index=index,
            condition=filter,
            attributes=attributes,
            segment=segment,
            total_segments=total_segments,
            page_size=page_size,
        )
        return ItemStream(self._session, table, "Scan", request)

    async def batch_get_items(
        self,
        requests: Mapping[str, Sequence[ItemKey]],
        *,
        consistent_read: bool = False,
    ) -> dict[str, list[Item]]:
        """Read many items, from one table or several, a hundred keys at a time.

        Returns:
            The items found, by table. **Missing keys are simply absent** — the result may be
            shorter than what was asked for, and that is DynamoDB's answer rather than a
            failure.

        Raises:
            BatchIncompleteError: If the service was still declining keys after the retry
                budget. Without this the answer would be short and look complete.
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        found: dict[str, list[Item]] = {table: [] for table in requests}
        for table, keys in requests.items():
            for chunk in chunked(list(keys), READ_BATCH_SIZE):
                await read_batch(
                    self._session,
                    {
                        table: {
                            "Keys": [serialise(key) for key in chunk],
                            "ConsistentRead": consistent_read,
                        }
                    },
                    found,
                )
        return found

    async def batch_write_items(
        self, requests: Mapping[str, Sequence[WriteRequest]]
    ) -> None:
        """Write and delete many items, twenty-five at a time.

        **Retries what the service declined.** DynamoDB answers 200 with an `UnprocessedItems`
        map when it throttles, and a client that ignores it loses those writes without saying so.

        Raises:
            BatchIncompleteError: If entries were still unprocessed after the retry budget.
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        for table, writes in requests.items():
            for chunk in chunked(list(writes), WRITE_BATCH_SIZE):
                await write_batch(
                    self._session,
                    {table: [_requests.write_entry(write) for write in chunk]},
                )

    async def transact_write_items(
        self, items: Sequence[TransactWrite], *, request_token: str | None = None
    ) -> None:
        """Apply every write, or none of them.

        Args:
            items: Up to a hundred puts, updates, deletes and condition checks.
            request_token: An idempotency token. **Never invented here** — see
                `dexter.aws.dynamodb.transactions`.

        Raises:
            ValueError: If `items` is empty or longer than a hundred.
            ConditionFailedError: If any condition was false, naming the entry's index.
            TransactionConflictError: If another transaction touched the same item. Retryable,
                where a failed condition is not.
            AwsRequestError: If the transaction was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await transact_write(self._session, items, request_token)

    async def transact_get_items(
        self, items: Sequence[TransactGet]
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
        return await transact_get(self._session, items)
