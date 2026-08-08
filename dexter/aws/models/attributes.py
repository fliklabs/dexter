"""Naming an attribute or a key, and building a condition on it.

The public vocabulary. Everything here returns a node from `conditions.py`; nothing here knows
how one is compiled.

**`Key` and `Attr` are separate classes, and that is the improvement over the reference.** A key
condition legally supports seven operators; a filter supports thirteen. Making them one class
means `Key("sk").contains("x")` type-checks and then fails as a service error at run time. Two
classes make it a type error, which is the same fact discovered several hours earlier.
"""

from typing import Any

from .conditions import (
    AttributeType,
    BeginsWith,
    Between,
    Comparison,
    ComparisonOperator,
    Condition,
    Contains,
    Exists,
    In,
    NotExists,
)


class Key:
    """Builds conditions on a table's partition or sort key.

    **Only the seven operators a key condition allows.** DynamoDB requires the partition key to
    be compared with equality, and permits the sort key to use a range comparison or a prefix;
    anything else is refused by the service. Offering only those makes the refusal a type error.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        """Name the key attribute."""
        self._name = name

    def equals(self, value: Any, /) -> Condition:
        """The key equals `value`. The only comparison a partition key allows."""
        return Comparison(self._name, ComparisonOperator.EQUALS, value, is_key=True)

    def less_than(self, value: Any, /) -> Condition:
        """The sort key is below `value`."""
        return Comparison(self._name, ComparisonOperator.LESS_THAN, value, is_key=True)

    def less_or_equal(self, value: Any, /) -> Condition:
        """The sort key is at or below `value`."""
        return Comparison(
            self._name, ComparisonOperator.LESS_OR_EQUAL, value, is_key=True
        )

    def greater_than(self, value: Any, /) -> Condition:
        """The sort key is above `value`."""
        return Comparison(
            self._name, ComparisonOperator.GREATER_THAN, value, is_key=True
        )

    def greater_or_equal(self, value: Any, /) -> Condition:
        """The sort key is at or above `value`."""
        return Comparison(
            self._name, ComparisonOperator.GREATER_OR_EQUAL, value, is_key=True
        )

    def between(self, low: Any, high: Any, /) -> Condition:
        """The sort key is within an inclusive range."""
        return Between(self._name, low, high, is_key=True)

    def begins_with(self, prefix: str, /) -> Condition:
        """The sort key starts with `prefix`.

        The operator single-table designs are built on: `Key("sk").begins_with("order#")`
        selects one kind of related item out of a partition holding several.
        """
        return BeginsWith(self._name, prefix, is_key=True)


class Attr:
    """Builds conditions on any attribute, for a filter or a conditional write."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        """Name the attribute."""
        self._name = name

    def equals(self, value: Any, /) -> Condition:
        """The attribute equals `value`."""
        return Comparison(self._name, ComparisonOperator.EQUALS, value)

    def not_equals(self, value: Any, /) -> Condition:
        """The attribute does not equal `value`."""
        return Comparison(self._name, ComparisonOperator.NOT_EQUALS, value)

    def less_than(self, value: Any, /) -> Condition:
        """The attribute is below `value`."""
        return Comparison(self._name, ComparisonOperator.LESS_THAN, value)

    def less_or_equal(self, value: Any, /) -> Condition:
        """The attribute is at or below `value`."""
        return Comparison(self._name, ComparisonOperator.LESS_OR_EQUAL, value)

    def greater_than(self, value: Any, /) -> Condition:
        """The attribute is above `value`."""
        return Comparison(self._name, ComparisonOperator.GREATER_THAN, value)

    def greater_or_equal(self, value: Any, /) -> Condition:
        """The attribute is at or above `value`."""
        return Comparison(self._name, ComparisonOperator.GREATER_OR_EQUAL, value)

    def between(self, low: Any, high: Any, /) -> Condition:
        """The attribute is within an inclusive range."""
        return Between(self._name, low, high)

    def begins_with(self, prefix: str, /) -> Condition:
        """The attribute starts with `prefix`."""
        return BeginsWith(self._name, prefix)

    def contains(self, value: Any, /) -> Condition:
        """The attribute contains `value` as a substring, a set member, or a list element."""
        return Contains(self._name, value)

    def exists(self) -> Condition:
        """The attribute is present."""
        return Exists(self._name)

    def not_exists(self) -> Condition:
        """The attribute is absent.

        The same as `~Attr("x").exists()`, and worth having under its own name because
        "only if it is not already there" is the most common conditional write there is.
        """
        return NotExists(self._name)

    def is_in(self, values: tuple[Any, ...], /) -> Condition:
        """The attribute equals one of `values`.

        Named `is_in` rather than `in_`, because `in` is a keyword and a trailing underscore
        reads like an afterthought.
        """
        return In(self._name, values)

    def attribute_type(self, type_code: str, /) -> Condition:
        """The attribute is stored as `type_code` — `S`, `N`, `B`, `BOOL`, `L`, `M`, `NULL`."""
        return AttributeType(self._name, type_code)
