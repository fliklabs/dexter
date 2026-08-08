"""Assembling what goes on the wire.

One file for turning dexter's own types into DynamoDB's request shapes, so that the client is
the list of operations and this is how each one is spelled. Everything here is pure: it takes
values, returns dictionaries, and reaches no network.

The four entry builders exist because a batch write and a transaction both take a *list of
differently shaped things*, and matching on the type is how a `PutRequest` and a `DeleteRequest`
become the two keys the API wants without the caller ever seeing either name.
"""

from collections.abc import Sequence
from typing import Any

from ..models import (
    Condition,
    DeleteRequest,
    PutRequest,
    TransactConditionCheck,
    TransactDelete,
    TransactGet,
    TransactPut,
    TransactUpdate,
    TransactWrite,
    WriteRequest,
)
from ._expressions import (
    Expression,
    compile_condition,
    compile_pair,
    compile_update,
    merge,
)
from ._items import serialise


def compile_projection(attributes: Sequence[str], /) -> Expression:
    """The projection expression for `attributes`, with reserved words escaped.

    Every attribute goes through a placeholder rather than only the ones that need it: DynamoDB
    reserves several hundred words including `name`, `status` and `size`, and a caller should
    not have to know the list. The `#p` prefix keeps these clear of the `#n` the condition
    builder allocates and the `#u` an update allocates.
    """
    names = {f"#p{index}": name for index, name in enumerate(attributes)}
    return Expression(", ".join(names), names, {})


def apply_condition(request: dict[str, Any], condition: Condition | None, /) -> None:
    """Attach a condition to a request, if there is one."""
    if condition is None:
        return
    compiled = compile_condition(condition)
    request["ConditionExpression"] = compiled.expression
    if compiled.names:
        request["ExpressionAttributeNames"] = compiled.names
    if compiled.values:
        request["ExpressionAttributeValues"] = compiled.values


def write_entry(write: WriteRequest, /) -> dict[str, Any]:
    """One `BatchWriteItem` entry."""
    match write:
        case PutRequest(item=item):
            return {"PutRequest": {"Item": serialise(item)}}
        case DeleteRequest(key=key):
            return {"DeleteRequest": {"Key": serialise(key)}}


def transact_entry(item: TransactWrite, /) -> dict[str, Any]:
    """One `TransactWriteItems` entry."""
    match item:
        case TransactPut(table=table, item=document, condition=condition):
            entry: dict[str, Any] = {"TableName": table, "Item": serialise(document)}
            apply_condition(entry, condition)
            return {"Put": entry}
        case TransactDelete(table=table, key=key, condition=condition):
            entry = {"TableName": table, "Key": serialise(key)}
            apply_condition(entry, condition)
            return {"Delete": entry}
        case TransactConditionCheck(table=table, key=key, condition=condition):
            entry = {"TableName": table, "Key": serialise(key)}
            apply_condition(entry, condition)
            return {"ConditionCheck": entry}
        case TransactUpdate():
            return {"Update": _transact_update_entry(item)}


def transact_get_entry(item: TransactGet, /) -> dict[str, Any]:
    """One `TransactGetItems` entry."""
    entry: dict[str, Any] = {"TableName": item.table, "Key": serialise(item.key)}
    if item.attributes:
        projection = compile_projection(item.attributes)
        entry["ProjectionExpression"] = projection.expression
        entry["ExpressionAttributeNames"] = projection.names
    return {"Get": entry}


def update_request(  # noqa: PLR0913 - mirrors `update_item`, where the count is argued
    table: str,
    key: dict[str, Any],
    *,
    set_values: dict[str, Any] | None,
    remove: tuple[str, ...],
    add: dict[str, Any] | None,
    delete: dict[str, Any] | None,
    condition: Condition | None,
    return_updated: bool,
) -> dict[str, Any]:
    """One `UpdateItem` request, with its update and its condition sharing both maps.

    Compiled separately and merged, which is safe only because the two allocate different
    placeholder prefixes — `#u0`/`:u0` here against the condition builder's `#n0`/`:v0`.

    Raises:
        ValueError: If nothing was asked to change.
        ItemEncodingError: If a value cannot be stored.
    """
    update = compile_update(
        set_values=set_values, remove=remove, add=add, delete=delete
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
    return request


def _transact_update_entry(item: TransactUpdate, /) -> dict[str, Any]:
    """The `Update` half of a transaction entry.

    Its own function because it is the only entry kind that compiles two expressions, and
    inlining it made the `match` above three times the length of every other clause.
    """
    update = compile_update(
        set_values=item.set_values,
        remove=item.remove,
        add=item.add,
        delete=item.delete,
    )
    check = compile_condition(item.condition) if item.condition is not None else None
    names, values = merge(update, check)

    entry: dict[str, Any] = {
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
    return entry


def query_request(  # noqa: PLR0913 - mirrors `query`, which is where the count is argued
    table: str,
    key_condition: Condition,
    *,
    index: str | None,
    condition: Condition | None,
    attributes: Sequence[str] | None,
    ascending: bool,
    consistent_read: bool,
    page_size: int,
) -> dict[str, Any]:
    """One `Query` request.

    The key condition and the filter are compiled together — `compile_pair` shares one builder
    between them, because two fresh ones both allocate `#n0` and `:v0` and merging their maps
    would silently discard half of each.
    """
    keys, rest = compile_pair(key_condition, condition)
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
    return request


def scan_request(  # noqa: PLR0913 - mirrors `scan`, which is where the count is argued
    table: str,
    *,
    index: str | None,
    condition: Condition | None,
    attributes: Sequence[str] | None,
    segment: int | None,
    total_segments: int | None,
    page_size: int,
) -> dict[str, Any]:
    """One `Scan` request."""
    rest = compile_condition(condition) if condition is not None else None
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
    return request


def get_request(
    table: str,
    key: dict[str, Any],
    *,
    consistent_read: bool,
    attributes: Sequence[str] | None,
) -> dict[str, Any]:
    """One `GetItem` request."""
    request: dict[str, Any] = {
        "TableName": table,
        "Key": serialise(key),
        "ConsistentRead": consistent_read,
    }
    if attributes:
        projection = compile_projection(attributes)
        request["ProjectionExpression"] = projection.expression
        request["ExpressionAttributeNames"] = projection.names
    return request
