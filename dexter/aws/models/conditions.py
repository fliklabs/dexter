"""Conditions over items: what to match, and what a write insists on.

**dexter's own expression tree, rather than boto3's.** `boto3.dynamodb.conditions.ConditionBase`
on a public signature would mean every consumer importing from boto3 to call a dexter method,
which is the one rule this module is built around. What is *not* rebuilt is the hard part:
`dexter/aws/dynamodb/_expressions.py` compiles this tree into boto3's own objects and hands them
to its `ConditionExpressionBuilder`, so placeholder allocation and reserved-word escaping stay
where they are already correct.

**`Key` and `Attr` are separate classes, and that is the improvement over the reference.** A key
condition legally supports seven operators; a filter supports thirteen. Making them one class
means `Key("sk").contains("x")` type-checks and then fails as a service error at run time. Two
classes make it a type error, which is the same fact discovered several hours earlier.

Compose with `&`, `|` and `~`::

    Attr("status").equals("PAID") & ~Attr("cancelled_at").exists()
"""

from enum import StrEnum
from typing import Any


class ComparisonOperator(StrEnum):
    """How two values are compared."""

    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    LESS_THAN = "LESS_THAN"
    LESS_OR_EQUAL = "LESS_OR_EQUAL"
    GREATER_THAN = "GREATER_THAN"
    GREATER_OR_EQUAL = "GREATER_OR_EQUAL"


def describe_comparison_operator(operator: ComparisonOperator, /) -> str:
    """Render a comparison operator as the symbol a caller would type."""
    return f"ComparisonOperator.{operator.name}"


class Condition:
    """One test an item either passes or does not.

    Never constructed directly — `Attr` and `Key` build these, and the operators compose them.
    """

    __slots__ = ()

    def __and__(self, other: Condition) -> Condition:
        """Both must hold."""
        return And(self, other)

    def __or__(self, other: Condition) -> Condition:
        """Either must hold."""
        return Or(self, other)

    def __invert__(self) -> Condition:
        """The opposite must hold."""
        return Not(self)


class Comparison(Condition):
    """An attribute compared against one value."""

    __slots__ = ("is_key", "operator", "path", "value")

    def __init__(
        self,
        path: str,
        operator: ComparisonOperator,
        value: Any,
        *,
        is_key: bool = False,
    ) -> None:
        """Record the comparison."""
        self.path = path
        self.operator = operator
        self.value = value
        self.is_key = is_key


class Between(Condition):
    """An attribute within an inclusive range."""

    __slots__ = ("high", "is_key", "low", "path")

    def __init__(self, path: str, low: Any, high: Any, *, is_key: bool = False) -> None:
        """Record the range."""
        self.path = path
        self.low = low
        self.high = high
        self.is_key = is_key


class BeginsWith(Condition):
    """A string attribute starting with a prefix."""

    __slots__ = ("is_key", "path", "value")

    def __init__(self, path: str, value: str, *, is_key: bool = False) -> None:
        """Record the prefix."""
        self.path = path
        self.value = value
        self.is_key = is_key


class Contains(Condition):
    """A string attribute containing a substring, or a set containing a member."""

    __slots__ = ("path", "value")

    def __init__(self, path: str, value: Any) -> None:
        """Record the member."""
        self.path = path
        self.value = value


class Exists(Condition):
    """An attribute that is present, whatever its value."""

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        """Record the attribute."""
        self.path = path


class NotExists(Condition):
    """An attribute that is absent.

    The condition that makes `put_item` an insert rather than an upsert: `~Attr("pk").exists()`
    says "only if nothing is there".
    """

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        """Record the attribute."""
        self.path = path


class In(Condition):
    """An attribute equal to one of several values."""

    __slots__ = ("path", "values")

    def __init__(self, path: str, values: tuple[Any, ...]) -> None:
        """Record the candidates."""
        self.path = path
        self.values = values


class AttributeType(Condition):
    """An attribute stored as a particular DynamoDB type, such as `S` or `N`."""

    __slots__ = ("path", "type")

    def __init__(self, path: str, type: str) -> None:  # noqa: A002
        """Record the type code."""
        self.path = path
        self.type = type


class And(Condition):
    """Both halves must hold."""

    __slots__ = ("left", "right")

    def __init__(self, left: Condition, right: Condition) -> None:
        """Record the halves."""
        self.left = left
        self.right = right


class Or(Condition):
    """Either half must hold."""

    __slots__ = ("left", "right")

    def __init__(self, left: Condition, right: Condition) -> None:
        """Record the halves."""
        self.left = left
        self.right = right


class Not(Condition):
    """The inner condition must not hold."""

    __slots__ = ("inner",)

    def __init__(self, inner: Condition) -> None:
        """Record the condition to negate."""
        self.inner = inner


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
