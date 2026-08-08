"""Turning Python values into DynamoDB's wire form, and back.

**One readable file holding the whole type policy**, which is the main reason this module talks
to the client API rather than the resource API. The resource API applies the same serializer
through an event handler nobody can see, so what `Decimal`, `float`, `set` and `bytes` do becomes
an emergent property of boto3 rather than a decision with a test.

The policy, and what each rule is for:

| Python | Stored | Read back |
| --- | --- | --- |
| `None` | `NULL` | `None` |
| `bool` | `BOOL` | `bool` |
| `int`, `Decimal` | `N` | `int` or `Decimal` |
| `float` | **refused** | — |
| `str` | `S` | `str` |
| `bytes`, `bytearray` | `B` | `bytes` |
| `set` of one kind | `SS` / `NS` / `BS` | `set` |
| empty `set` | **refused** | — |
| `list`, `tuple` | `L` | `list` |
| `Mapping` | `M` | `dict` |
| anything else | **refused** | — |

**`float` is refused rather than converted.** A price stored through binary floating point is
`0.30000000000000004`, and it is stored that way silently. Converting for the caller would be
choosing a rounding they did not ask for; refusing tells them to hand over a `Decimal`, which is
the only correct answer.

**`bytes` comes back as `bytes`.** boto3's deserializer wraps binary in its own `Binary` class,
which would be a boto3 type on a public return value — the one thing this module does not do.

Subclasses work throughout, because the serializer dispatches on `isinstance` and on
`collections.abc` rather than on exact type: an `IntEnum`, a `StrEnum`, a `frozenset` and a
`Mapping` that is not a `dict` all store correctly.
"""

from decimal import Decimal
from typing import Any, cast

from boto3.dynamodb.types import Binary, TypeDeserializer, TypeSerializer

from ..errors import ItemEncodingError
from ..models import Item

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def serialise(item: Item, /) -> dict[str, Any]:
    """One item in DynamoDB's wire form.

    Raises:
        ItemEncodingError: If any attribute holds something that cannot be stored. The message
            names the attribute and the type, because this is a bug in the calling code and the
            only useful thing to say is where it is.
    """
    return {name: serialise_value(value, name) for name, value in item.items()}


def serialise_value(value: Any, path: str, /) -> dict[str, Any]:
    """One value in DynamoDB's wire form.

    Args:
        value: What to store.
        path: Where it came from, used only to make the error readable.

    Raises:
        ItemEncodingError: If `value` cannot be stored.
    """
    # `_check` is the only guard, and it is exhaustive: it accepts exactly what the serializer
    # accepts and raises for everything else, naming the attribute path. Wrapping the call below
    # in a second `except TypeError` would be a branch nothing can reach — the serializer's own
    # refusals are all pre-empted — and this repository has no `# pragma: no cover` to excuse
    # one. If the two ever diverge, the test suite is where that shows up.
    _check(value, path)
    # The stubs type this as their own `_AttributeValueTypeDef`, which is structurally the wire
    # form and nominally a name no consumer can import. Cast rather than propagate it.
    return cast("dict[str, Any]", _SERIALIZER.serialize(value))


def deserialise(item: dict[str, Any], /) -> Item:
    """One item read back as ordinary Python values."""
    return {name: deserialise_value(value) for name, value in item.items()}


def deserialise_value(value: dict[str, Any], /) -> Any:
    """One wire value read back, with binary unwrapped."""
    return _unwrap(_DESERIALIZER.deserialize(cast("Any", value)))


def _check(value: Any, path: str, /) -> None:
    """Refuse what must not reach the serializer, recursing into containers.

    Done before serialising rather than after, so the error names the attribute path that
    actually holds the problem rather than the top-level attribute that contains it.
    """
    # `bool` first: it is a subclass of `int`, so a value check that tested numbers first would
    # classify `True` as a number and store `1`.
    if value is None or isinstance(value, bool | str | bytes | bytearray):
        return

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ItemEncodingError(
                f"{path} holds {value}, and DynamoDB stores only finite numbers. The service "
                f"refuses it as a validation error naming the whole item."
            )
        return

    if isinstance(value, float):
        raise ItemEncodingError(
            f"{path} holds a float ({value!r}). DynamoDB stores numbers exactly, and a float "
            f"cannot be converted without choosing a rounding — use a Decimal."
        )

    if isinstance(value, int):
        return

    if isinstance(value, set | frozenset):
        if not value:
            raise ItemEncodingError(
                f"{path} holds an empty set. DynamoDB has no empty set type, and the service "
                f"refuses one with a validation error naming nothing."
            )
        for member in value:
            _check(member, f"{path}[]")
        return

    if isinstance(value, list | tuple):
        for index, member in enumerate(value):
            _check(member, f"{path}[{index}]")
        return

    if isinstance(value, dict):
        for name, member in value.items():
            _check(member, f"{path}.{name}")
        return

    raise ItemEncodingError(
        f"{path} holds a {type(value).__name__}, which DynamoDB cannot store. Convert it to a "
        f"string, a number, or a mapping first."
    )


def _unwrap(value: Any, /) -> Any:
    """Normalise what boto3 hands back, recursing into containers.

    Two conversions, both so that what comes out looks like what went in:

    - **A whole number reads back as `int`.** boto3 answers every `N` with a `Decimal`, so a
      counter stored as `3` would otherwise return `Decimal("3")` — which compares equal to `3`
      and then fails the first time it meets `json.dumps` or a `%d`.
    - **Binary reads back as `bytes`**, not boto3's `Binary` wrapper, which would be a boto3
      type on a public return value.

    The asymmetry is deliberate and worth knowing: a value stored as `Decimal("3")` also comes
    back as `int`, because nothing in the stored form distinguishes it from `3`. Anything with a
    fractional part keeps its `Decimal`, which is the case that matters for money.
    """
    if isinstance(value, Decimal):
        # `exponent` is `'n'`, `'N'` or `'F'` for a NaN or an infinity rather than an integer.
        # Those cannot be stored, so they cannot be read back either — but the check keeps the
        # comparison honest rather than relying on that.
        exponent = value.as_tuple().exponent
        return int(value) if isinstance(exponent, int) and exponent >= 0 else value
    if isinstance(value, Binary):
        # `.value` is `Binary`'s documented attribute and the stubs do not declare it, so it is
        # read through an `Any` — the repo convention for a deliberately unchecked access.
        wrapper: Any = value
        return bytes(wrapper.value)
    if isinstance(value, dict):
        return {name: _unwrap(member) for name, member in value.items()}
    if isinstance(value, list):
        return [_unwrap(member) for member in value]
    if isinstance(value, set):
        return {_unwrap(member) for member in value}
    return value
