"""Conditions over items: the tree, and what each node means.

**dexter's own expression tree, rather than boto3's.** `boto3.dynamodb.conditions.ConditionBase`
on a public signature would mean every consumer importing from boto3 to call a dexter method,
which is the one rule this module is built around. What is *not* rebuilt is the hard part:
`dexter/aws/dynamodb/_expressions.py` compiles this tree into boto3's own objects and hands them
to its `ConditionExpressionBuilder`, so placeholder allocation and reserved-word escaping stay
where they are already correct.

These are the nodes. **`Attr` and `Key`, which build them, are in `attributes.py`** — the tree is
the representation and the builders are the vocabulary, and a reader wanting one rarely wants the
other. Nothing here is constructed directly; compose what the builders return with `&`, `|` and
`~`::

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
