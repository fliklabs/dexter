"""Compiling conditions and updates into what DynamoDB reads.

Two halves, both of which exist so that no caller ever writes an expression string.

**Conditions are compiled through boto3's own builder.** dexter's tree is translated into
`boto3.dynamodb.conditions` objects and handed to `ConditionExpressionBuilder`, which allocates
`#n0` / `:v0` placeholders and escapes reserved words — DynamoDB has several hundred, `status`
and `name` among them, and an unescaped one is a syntax error from the service. Rebuilding that
would be rebuilding the part that is already right; what dexter owns is the public vocabulary.

Two details the reference library gets wrong and this does not:

- **`is_key_condition=True` for a key condition.** Without it the builder produces an expression
  the service refuses, because a key condition permits a strictly smaller grammar.
- **The value placeholders are serialised.** The builder hands back ordinary Python values, and
  the client API wants the wire form.

**Update expressions are built here rather than taken as strings**, so that `SET`, `REMOVE`,
`ADD` and `DELETE` are four keyword arguments instead of a syntax a caller has to learn — and so
that the same placeholder machinery escapes reserved words in the names being written.
"""

from typing import Any

from boto3.dynamodb.conditions import (
    Attr as BotoAttr,
)
from boto3.dynamodb.conditions import (
    ConditionBase,
    ConditionExpressionBuilder,
)
from boto3.dynamodb.conditions import (
    Key as BotoKey,
)

from ..errors import ItemEncodingError
from ..models import (
    And,
    AttributeType,
    BeginsWith,
    Between,
    Comparison,
    ComparisonOperator,
    Condition,
    Contains,
    Exists,
    In,
    Not,
    NotExists,
    Or,
)
from ._items import serialise_value

COMPARISONS = {
    ComparisonOperator.EQUALS: "eq",
    ComparisonOperator.NOT_EQUALS: "ne",
    ComparisonOperator.LESS_THAN: "lt",
    ComparisonOperator.LESS_OR_EQUAL: "lte",
    ComparisonOperator.GREATER_THAN: "gt",
    ComparisonOperator.GREATER_OR_EQUAL: "gte",
}
"""dexter's operator names, and boto3's method for each.

A table so that a member added to `ComparisonOperator` without a translation is a `KeyError` here
rather than a silently missing clause.
"""


class Expression:
    """A compiled expression and the placeholder maps that go with it."""

    __slots__ = ("expression", "names", "values")

    def __init__(
        self,
        expression: str,
        names: dict[str, str],
        values: dict[str, Any],
    ) -> None:
        """Record the three parts a request needs."""
        self.expression = expression
        self.names = names
        self.values = values


def compile_condition(
    condition: Condition,
    /,
    *,
    is_key: bool = False,
    builder: ConditionExpressionBuilder | None = None,
) -> Expression:
    """Turn one condition into an expression, with its names and values.

    Args:
        condition: The condition to compile.
        is_key: Whether this is a key condition. **Not cosmetic** — a key condition has a
            smaller grammar, and the builder produces something the service refuses if it is
            not told.
        builder: A builder to share with other conditions in the same request. **Required
            whenever two conditions travel together**, which is what a query with both a key
            condition and a filter is: each builder counts from zero, so two fresh ones both
            allocate `#n0` and `:v0`, and merging their maps silently discards half of each.
            The result is a query that reads the right expression against the wrong values.

    Raises:
        ItemEncodingError: If a value in the condition cannot be stored.
    """
    built = (builder or ConditionExpressionBuilder()).build_expression(
        _translate(condition), is_key_condition=is_key
    )
    return Expression(
        expression=built.condition_expression,
        names=dict(built.attribute_name_placeholders),
        # The builder hands back ordinary Python values; the client API wants the wire form.
        values={
            placeholder: serialise_value(value, placeholder)
            for placeholder, value in built.attribute_value_placeholders.items()
        },
    )


def compile_pair(
    key_condition: Condition, rest: Condition | None, /
) -> tuple[Expression, Expression | None]:
    """Compile a key condition and an optional filter that travel in one request.

    **They must share a builder, and this function exists so that no caller has to know it.**
    Each `ConditionExpressionBuilder` counts from zero, so two fresh ones both allocate `#n0`
    and `:v0` — and merging their maps then discards half of each, leaving a query whose
    expressions are right and whose values belong to the other clause. Nothing about that fails
    loudly; it returns the wrong rows.

    Raises:
        ItemEncodingError: If a value in either condition cannot be stored.
    """
    shared = ConditionExpressionBuilder()
    keys = compile_condition(key_condition, is_key=True, builder=shared)
    if rest is None:
        return keys, None
    return keys, compile_condition(rest, builder=shared)


def compile_update(
    *,
    set_values: dict[str, Any] | None,
    remove: tuple[str, ...],
    add: dict[str, Any] | None,
    delete: dict[str, Any] | None,
) -> Expression:
    """Turn four keyword groups into one update expression.

    `SET` writes, `REMOVE` deletes attributes, `ADD` increments a number or extends a set, and
    `DELETE` takes members out of a set. They are separate arguments rather than one expression
    string because the four mean genuinely different things and the syntax joining them is not
    knowledge a caller should need.

    **Top-level attribute names only.** A dotted path is ambiguous with an attribute name that
    contains a dot, and telling them apart needs a path grammar — a separate concept, and one
    worth adding deliberately rather than by accident.

    Raises:
        ValueError: If every group is empty, which the service refuses as a malformed request.
        ItemEncodingError: If a value cannot be stored.
    """
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    clauses: list[str] = []

    def placeholder(name: str) -> str:
        token = f"#u{len(names)}"
        names[token] = name
        return token

    def bind(name: str, value: Any) -> str:
        token = f":u{len(values)}"
        values[token] = serialise_value(value, name)
        return token

    if set_values:
        clauses.append(
            "SET "
            + ", ".join(
                f"{placeholder(name)} = {bind(name, value)}"
                for name, value in set_values.items()
            )
        )
    if remove:
        clauses.append("REMOVE " + ", ".join(placeholder(name) for name in remove))
    if add:
        clauses.append(
            "ADD "
            + ", ".join(
                f"{placeholder(name)} {bind(name, value)}"
                for name, value in add.items()
            )
        )
    if delete:
        clauses.append(
            "DELETE "
            + ", ".join(
                f"{placeholder(name)} {bind(name, value)}"
                for name, value in delete.items()
            )
        )

    if not clauses:
        raise ValueError(
            "An update must change something: give at least one of set_values, remove, add "
            "or delete."
        )
    return Expression(" ".join(clauses), names, values)


def merge(*expressions: Expression | None) -> tuple[dict[str, str], dict[str, Any]]:
    """The combined name and value maps of several expressions.

    An update and its condition are compiled separately and travel in one request, sharing both
    placeholder maps. The prefixes keep them apart: `compile_update` allocates `#u0` and `:u0`,
    where boto3's builder allocates `#n0` and `:v0`, so a collision is impossible rather than
    unlikely.
    """
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for expression in expressions:
        if expression is not None:
            names.update(expression.names)
            values.update(expression.values)
    return names, values


def _translate(condition: Condition, /) -> ConditionBase:  # noqa: PLR0911 - one clause per node type; a dispatch table would need a cast per entry
    """Dexter's tree as boto3's, so that its builder can render it.

    Raises:
        ItemEncodingError: If the tree holds a node this does not know, which can only happen
            if a node type was added without a clause here.
    """
    match condition:
        case Comparison(path=path, operator=operator, value=value, is_key=is_key):
            attribute = BotoKey(path) if is_key else BotoAttr(path)
            return getattr(attribute, COMPARISONS[operator])(value)  # type: ignore[no-any-return]
        case Between(path=path, low=low, high=high, is_key=is_key):
            attribute = BotoKey(path) if is_key else BotoAttr(path)
            return attribute.between(low, high)
        case BeginsWith(path=path, value=value, is_key=is_key):
            attribute = BotoKey(path) if is_key else BotoAttr(path)
            return attribute.begins_with(value)
        case Contains(path=path, value=value):
            return BotoAttr(path).contains(value)
        case Exists(path=path):
            return BotoAttr(path).exists()
        case NotExists(path=path):
            return BotoAttr(path).not_exists()
        case In(path=path, values=values):
            return BotoAttr(path).is_in(list(values))
        case AttributeType(path=path, type=type_code):
            return BotoAttr(path).attribute_type(type_code)
        case And(left=left, right=right):
            return _translate(left) & _translate(right)
        case Or(left=left, right=right):
            return _translate(left) | _translate(right)
        case Not(inner=inner):
            return ~_translate(inner)
        case _:
            raise ItemEncodingError(
                f"{type(condition).__name__} is not a condition this module can compile."
            )
