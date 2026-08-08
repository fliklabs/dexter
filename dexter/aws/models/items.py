"""What a document store takes in and hands back.

**These are `NamedTuple`s and plain aliases, not pydantic models**, and AGENTS.md's own split is
the reason. A pydantic model must be frozen and hashable to be worth having, and every type here
carries a `dict` of arbitrary attributes — which makes a frozen model unhashable while validating
nothing, since the inside of an item is exactly what pydantic cannot check. A tuple gives
equality and a readable `repr` for free, constructs in nanoseconds, and is what a batch of two
hundred writes should be made of.

Validation of an item's *contents* happens where it can be meaningful:
`dexter/aws/dynamodb/_items.py` refuses a `float`, an empty set and an unstorable type before any
request is made.
"""

from typing import Any, NamedTuple

from .conditions import Condition

type Item = dict[str, Any]
"""One document, as ordinary Python values.

Never DynamoDB's `{"S": "..."}` wire form — that shape is converted at the boundary and appears
on no signature in this module.
"""

type ItemKey = dict[str, Any]
"""The partition key, and the sort key where the table has one.

The same shape as an `Item` and a different thing, which is why it has its own name: passing a
whole item where a key is expected is accepted by the service and quietly means something else.
"""


class ItemPage(NamedTuple):
    """One response from a query or a scan."""

    items: tuple[Item, ...]
    last_key: ItemKey | None
    """Where the next page starts, or `None` when this was the last.

    **The only reliable end-of-results signal.** A filtered query routinely returns an empty
    `items` while more pages remain — the filter is applied after the read, so a page can be
    entirely filtered out — which is why "stop when the page is empty" is wrong.
    """


class PutRequest(NamedTuple):
    """Write one item, in a batch."""

    item: Item


class DeleteRequest(NamedTuple):
    """Remove one item, in a batch."""

    key: ItemKey


type WriteRequest = PutRequest | DeleteRequest
"""One entry of a batch write. DynamoDB allows no update in a batch, so there is no third."""


class TransactPut(NamedTuple):
    """Write one item as part of a transaction."""

    table: str
    item: Item
    condition: Condition | None = None


class TransactDelete(NamedTuple):
    """Remove one item as part of a transaction."""

    table: str
    key: ItemKey
    condition: Condition | None = None


class TransactUpdate(NamedTuple):
    """Change one item as part of a transaction."""

    table: str
    key: ItemKey
    set_values: dict[str, Any] | None = None
    remove: tuple[str, ...] = ()
    add: dict[str, Any] | None = None
    delete: dict[str, Any] | None = None
    condition: Condition | None = None


class TransactConditionCheck(NamedTuple):
    """Assert something about an item without changing it.

    What makes a transaction span more than the items it writes: "move this order to PAID, but
    only if the customer is still active" touches the customer without modifying it.
    """

    table: str
    key: ItemKey
    condition: Condition


type TransactWrite = (
    TransactPut | TransactUpdate | TransactDelete | TransactConditionCheck
)
"""One entry of a transactional write."""


class TransactGet(NamedTuple):
    """Read one item as part of a transaction."""

    table: str
    key: ItemKey
    attributes: tuple[str, ...] = ()
