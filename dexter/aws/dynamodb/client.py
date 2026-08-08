"""Reading and writing documents.

Ten operations over the client API. Three things run through all of them:

- **Items are ordinary Python values on the way in and out.** DynamoDB's `{"S": "..."}` wire form
  appears on no signature; `_items.py` owns the conversion and the policy behind it.
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

from botocore.exceptions import ClientError

from .._calling import call, error_code
from ..errors import (
    BatchIncompleteError,
    ConditionFailedError,
    TransactionConflictError,
)
from ..models import (
    Condition,
    DeleteRequest,
    Item,
    ItemKey,
    PutRequest,
    TransactConditionCheck,
    TransactDelete,
    TransactGet,
    TransactPut,
    TransactUpdate,
    TransactWrite,
    WriteRequest,
)
from ..session import AwsSession
from ._batching import backoff, chunked, count_unprocessed
from ._expressions import (
    Expression,
    compile_condition,
    compile_pair,
    compile_update,
    merge,
)
from ._items import deserialise, serialise
from .paging import ItemStream

CONDITION_FAILED_CODE = "ConditionalCheckFailedException"
"""What DynamoDB says when a single conditional write's condition was false."""

CANCELLED_CONDITION_CODE = "ConditionalCheckFailed"
"""The same failure, as a transaction's cancellation reason.

**Not the same string**, and the missing `Exception` suffix is the whole trap: a transaction is
refused with `TransactionCanceledException`, and the per-entry reasons inside it use this shorter
code. Comparing the reasons against the exception code above matches nothing, so every cancelled
transaction would surface as a bare request error with the cause buried in an array nobody read.
"""

WRITE_BATCH_SIZE = 25
"""How many entries one `BatchWriteItem` may carry. A hard service limit."""

READ_BATCH_SIZE = 100
"""How many keys one `BatchGetItem` may carry. A hard service limit."""

TRANSACTION_SIZE = 100
"""How many entries one transaction may carry. Raised from 25 by the service in 2022."""


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
        request: dict[str, Any] = {
            "TableName": table,
            "Key": serialise(key),
            "ConsistentRead": consistent_read,
        }
        if attributes:
            projection = compile_projection(attributes)
            request["ProjectionExpression"] = projection.expression
            request["ExpressionAttributeNames"] = projection.names

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
        _apply_condition(request, condition)

        await call(f"PutItem {table}", lambda: self._put(request))

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
        update = compile_update(
            set_values=dict(set_values) if set_values else None,
            remove=tuple(remove),
            add=dict(add) if add else None,
            delete=dict(delete) if delete else None,
        )
        check = compile_condition(condition) if condition is not None else None
        names, values = merge(update, check)

        request: dict[str, Any] = {
            "TableName": table,
            "Key": serialise(key),
            "UpdateExpression": update.expression,
            "ReturnValues": "ALL_NEW" if return_updated else "NONE",
        }
        if check is not None:
            request["ConditionExpression"] = check.expression
        if names:
            request["ExpressionAttributeNames"] = names
        if values:
            request["ExpressionAttributeValues"] = values

        response = await call(f"UpdateItem {table}", lambda: self._update(request))
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
        _apply_condition(request, condition)

        await call(f"DeleteItem {table}", lambda: self._delete(request))

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
        keys, rest = compile_pair(key_condition, filter)
        projection = compile_projection(attributes) if attributes else None
        names, values = merge(keys, rest, projection)

        request: dict[str, Any] = {
            "TableName": table,
            "KeyConditionExpression": keys.expression,
            "ScanIndexForward": ascending,
            "ConsistentRead": consistent_read,
            "Limit": page_size,
        }
        if index is not None:
            request["IndexName"] = index
        if rest is not None:
            request["FilterExpression"] = rest.expression
        if projection is not None:
            request["ProjectionExpression"] = projection.expression
        if names:
            request["ExpressionAttributeNames"] = names
        if values:
            request["ExpressionAttributeValues"] = values

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
        rest = compile_condition(filter) if filter is not None else None
        projection = compile_projection(attributes) if attributes else None
        names, values = merge(rest, projection)

        request: dict[str, Any] = {"TableName": table, "Limit": page_size}
        if index is not None:
            request["IndexName"] = index
        if rest is not None:
            request["FilterExpression"] = rest.expression
        if projection is not None:
            request["ProjectionExpression"] = projection.expression
        if segment is not None:
            request["Segment"] = segment
        if total_segments is not None:
            request["TotalSegments"] = total_segments
        if names:
            request["ExpressionAttributeNames"] = names
        if values:
            request["ExpressionAttributeValues"] = values

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
        pending: list[dict[str, Any]] = []
        for table, keys in requests.items():
            pending.extend(
                {
                    table: {
                        "Keys": [serialise(key) for key in chunk],
                        "ConsistentRead": consistent_read,
                    }
                }
                for chunk in chunked(list(keys), READ_BATCH_SIZE)
            )

        for chunk in pending:
            await self._read_batch(chunk, found)
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
        entries: list[dict[str, Any]] = []
        for table, writes in requests.items():
            entries.extend(
                {table: [_write_entry(write) for write in chunk]}
                for chunk in chunked(list(writes), WRITE_BATCH_SIZE)
            )

        for chunk in entries:
            await self._write_batch(chunk)

    async def transact_write_items(
        self, items: Sequence[TransactWrite], *, request_token: str | None = None
    ) -> None:
        """Apply every write, or none of them.

        Args:
            items: Up to a hundred puts, updates, deletes and condition checks.
            request_token: An idempotency token. **Never invented here** — one dexter generated
                would differ on the caller's own retry, which is precisely when idempotency is
                supposed to help, so generating one would be a lie about a guarantee.

        Raises:
            ValueError: If `items` is empty or longer than a hundred.
            ConditionFailedError: If any condition was false. The message names the index of the
                entry that failed.
            TransactionConflictError: If another transaction touched the same item. Retryable,
                where a failed condition is not.
            AwsRequestError: If the transaction was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        if not items:
            raise ValueError("A transaction must contain at least one write.")
        if len(items) > TRANSACTION_SIZE:
            raise ValueError(
                f"A transaction may hold at most {TRANSACTION_SIZE} entries, and this has "
                f"{len(items)}."
            )

        request: dict[str, Any] = {
            "TransactItems": [_transact_entry(item) for item in items]
        }
        if request_token is not None:
            request["ClientRequestToken"] = request_token

        def write() -> Any:
            try:
                return self._session.dynamodb.transact_write_items(**request)
            except ClientError as error:
                _translate_cancellation(error)
                raise

        await call("TransactWriteItems", write)

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
        if not items:
            raise ValueError("A transactional read must name at least one item.")
        if len(items) > TRANSACTION_SIZE:
            raise ValueError(
                f"A transactional read may name at most {TRANSACTION_SIZE} items, and this "
                f"names {len(items)}."
            )

        entries = [_transact_get_entry(item) for item in items]

        def read() -> Any:
            try:
                return self._session.dynamodb.transact_get_items(TransactItems=entries)  # type: ignore[arg-type]
            except ClientError as error:
                _translate_cancellation(error)
                raise

        response = await call("TransactGetItems", read)
        return [
            deserialise(entry["Item"]) if entry.get("Item") else None
            for entry in response.get("Responses", [])
        ]

    # ── the private halves ───────────────────────────────────────────
    #
    # Each conditional write catches `ClientError` inside `work`, before `_calling.call` sees
    # it, because `ConditionalCheckFailedException` is the one code whose meaning depends on
    # which operation raised it — a shared table in `_calling.py` could not say which condition
    # was not met.

    def _put(self, request: dict[str, Any], /) -> Any:
        try:
            return self._session.dynamodb.put_item(**request)
        except ClientError as error:
            _translate_condition(error, "The condition on the put was not met.")
            raise

    def _update(self, request: dict[str, Any], /) -> Any:
        try:
            return self._session.dynamodb.update_item(**request)
        except ClientError as error:
            _translate_condition(error, "The condition on the update was not met.")
            raise

    def _delete(self, request: dict[str, Any], /) -> Any:
        try:
            return self._session.dynamodb.delete_item(**request)
        except ClientError as error:
            _translate_condition(error, "The condition on the delete was not met.")
            raise

    async def _write_batch(self, entries: dict[str, Any], /) -> None:
        """Send one chunk, resending whatever the service declined."""
        pending = entries
        for round_number in range(self._session.config.max_attempts):
            if round_number:
                await backoff(round_number)
            response = await self._send_write_batch(pending)
            unprocessed = response.get("UnprocessedItems") or {}
            if not unprocessed:
                return
            pending = unprocessed

        raise BatchIncompleteError(
            f"BatchWriteItem left {count_unprocessed(pending)} entries unprocessed after "
            f"{self._session.config.max_attempts} attempts."
        )

    async def _read_batch(
        self, keys: dict[str, Any], found: dict[str, list[Item]], /
    ) -> None:
        """Send one chunk of reads, resending whatever the service declined."""
        pending = keys
        for round_number in range(self._session.config.max_attempts):
            if round_number:
                await backoff(round_number)
            response = await self._send_read_batch(pending)
            for table, items in response.get("Responses", {}).items():
                found.setdefault(table, []).extend(deserialise(item) for item in items)
            unprocessed = response.get("UnprocessedKeys") or {}
            if not unprocessed:
                return
            pending = unprocessed

        raise BatchIncompleteError(
            f"BatchGetItem left {count_unprocessed(pending)} keys unprocessed after "
            f"{self._session.config.max_attempts} attempts."
        )

    async def _send_write_batch(self, pending: dict[str, Any], /) -> Any:
        """One `BatchWriteItem` request.

        Its own method rather than a lambda built inside the retry loop, because a closure over
        a loop variable is what ruff's B023 exists to catch.
        """
        return await call(
            "BatchWriteItem",
            lambda: self._session.dynamodb.batch_write_item(RequestItems=pending),
        )

    async def _send_read_batch(self, pending: dict[str, Any], /) -> Any:
        """One `BatchGetItem` request, for the same reason."""
        return await call(
            "BatchGetItem",
            lambda: self._session.dynamodb.batch_get_item(RequestItems=pending),
        )


def compile_projection(attributes: Sequence[str], /) -> Expression:
    """The projection expression for `attributes`, with reserved words escaped.

    Every attribute goes through a placeholder rather than only the ones that need it: DynamoDB
    reserves several hundred words including `name`, `status` and `size`, and a caller should
    not have to know the list. The `#p` prefix keeps these clear of the `#n` the condition
    builder allocates and the `#u` an update allocates.
    """
    names = {f"#p{index}": name for index, name in enumerate(attributes)}
    return Expression(", ".join(names), names, {})


def _apply_condition(request: dict[str, Any], condition: Condition | None, /) -> None:
    """Attach a condition to a request, if there is one."""
    if condition is None:
        return
    compiled = compile_condition(condition)
    request["ConditionExpression"] = compiled.expression
    if compiled.names:
        request["ExpressionAttributeNames"] = compiled.names
    if compiled.values:
        request["ExpressionAttributeValues"] = compiled.values


def _translate_condition(error: ClientError, message: str, /) -> None:
    """Raise `ConditionFailedError` if this is a failed condition, otherwise return."""
    if error_code(error) == CONDITION_FAILED_CODE:
        raise ConditionFailedError(message) from error


def _translate_cancellation(error: ClientError, /) -> None:
    """Turn a cancelled transaction into the specific reason it was cancelled.

    `TransactionCanceledException` carries a `CancellationReasons` array with one entry per
    item, and the entry that is not `None` is the one that matters. Without reading it a caller
    cannot tell a lost condition — which must not be retried — from a conflict, which should be.
    """
    reasons = error.response.get("CancellationReasons") or []
    for index, reason in enumerate(reasons):
        code = reason.get("Code")
        if code == CANCELLED_CONDITION_CODE:
            raise ConditionFailedError(
                f"The transaction was cancelled: the condition on entry {index} was not met."
            ) from error
        if code == "TransactionConflict":
            raise TransactionConflictError(
                f"The transaction was cancelled: another transaction is changing entry "
                f"{index}."
            ) from error


def _write_entry(write: WriteRequest, /) -> dict[str, Any]:
    """One `BatchWriteItem` entry."""
    match write:
        case PutRequest(item=item):
            return {"PutRequest": {"Item": serialise(item)}}
        case DeleteRequest(key=key):
            return {"DeleteRequest": {"Key": serialise(key)}}


def _transact_entry(item: TransactWrite, /) -> dict[str, Any]:
    """One `TransactWriteItems` entry."""
    match item:
        case TransactPut(table=table, item=document, condition=condition):
            entry: dict[str, Any] = {"TableName": table, "Item": serialise(document)}
            _apply_condition(entry, condition)
            return {"Put": entry}
        case TransactDelete(table=table, key=key, condition=condition):
            entry = {"TableName": table, "Key": serialise(key)}
            _apply_condition(entry, condition)
            return {"Delete": entry}
        case TransactConditionCheck(table=table, key=key, condition=condition):
            entry = {"TableName": table, "Key": serialise(key)}
            _apply_condition(entry, condition)
            return {"ConditionCheck": entry}
        case TransactUpdate():
            update = compile_update(
                set_values=item.set_values,
                remove=item.remove,
                add=item.add,
                delete=item.delete,
            )
            check = (
                compile_condition(item.condition)
                if item.condition is not None
                else None
            )
            names, values = merge(update, check)
            entry = {
                "TableName": item.table,
                "Key": serialise(item.key),
                "UpdateExpression": update.expression,
            }
            if check is not None:
                entry["ConditionExpression"] = check.expression
            if names:
                entry["ExpressionAttributeNames"] = names
            if values:
                entry["ExpressionAttributeValues"] = values
            return {"Update": entry}


def _transact_get_entry(item: TransactGet, /) -> dict[str, Any]:
    """One `TransactGetItems` entry."""
    entry: dict[str, Any] = {"TableName": item.table, "Key": serialise(item.key)}
    if item.attributes:
        projection = compile_projection(item.attributes)
        entry["ProjectionExpression"] = projection.expression
        entry["ExpressionAttributeNames"] = projection.names
    return {"Get": entry}
