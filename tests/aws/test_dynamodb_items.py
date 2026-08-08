"""Covers `dexter.aws.dynamodb._items`: what may be stored, and what comes back.

Every rule here is a correction to the obvious implementation, and each one fails silently
without a test: a `float` stored as `0.30000000000000004`, a `Decimal` read back as a float, a
`frozenset` refused because the dispatch was on exact type, an empty set refused by the service
with a message naming nothing, and binary handed back inside a boto3 wrapper class.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Any

import pytest

from dexter.aws import ItemEncodingError
from dexter.aws.dynamodb._items import deserialise, serialise


class Colour(StrEnum):
    RED = "RED"


class Size(IntEnum):
    LARGE = 3


def roundtrip(value: Any, /) -> Any:
    """Store one value and read it straight back."""
    return deserialise(serialise({"x": value}))["x"]


class TestScalars:
    def test_a_string_survives(self) -> None:
        assert roundtrip("hello") == "hello"

    def test_an_integer_survives_as_an_integer(self) -> None:
        assert roundtrip(7) == 7
        assert isinstance(roundtrip(7), int)

    def test_none_survives(self) -> None:
        assert roundtrip(None) is None

    def test_a_boolean_is_stored_as_a_boolean_not_a_number(self) -> None:
        """`bool` is a subclass of `int`, so a number check tried first stores `True` as `1`."""
        assert serialise({"x": True}) == {"x": {"BOOL": True}}
        assert roundtrip(True) is True

    def test_bytes_survive_as_bytes(self) -> None:
        """**Not boto3's `Binary` wrapper**, which would be a boto3 type on a public return."""
        result = roundtrip(b"\x00\x01")
        assert result == b"\x00\x01"
        assert isinstance(result, bytes)

    def test_a_bytearray_is_stored_as_binary(self) -> None:
        assert roundtrip(bytearray(b"ab")) == b"ab"


class TestNumbers:
    def test_a_decimal_survives_as_a_decimal(self) -> None:
        """**The correction that matters for money.**

        The obvious deserialiser answers `float(x) if "." in x else int(x)`, which turns every
        price into binary floating point on the way out.
        """
        result = roundtrip(Decimal("19.99"))
        assert result == Decimal("19.99")
        assert isinstance(result, Decimal)

    def test_a_float_is_refused_rather_than_rounded(self) -> None:
        """Converting silently would store a number the caller did not compute."""
        with pytest.raises(ItemEncodingError, match="Decimal"):
            serialise({"price": 19.99})

    def test_the_refusal_names_the_attribute(self) -> None:
        with pytest.raises(ItemEncodingError, match="price"):
            serialise({"price": 19.99})

    def test_a_float_nested_in_a_list_names_its_position(self) -> None:
        with pytest.raises(ItemEncodingError, match=r"prices\[1\]"):
            serialise({"prices": [Decimal(1), 2.5]})

    def test_a_float_nested_in_a_map_names_its_path(self) -> None:
        with pytest.raises(ItemEncodingError, match=r"detail\.price"):
            serialise({"detail": {"price": 2.5}})


class TestSubclasses:
    def test_a_string_enum_is_stored_as_a_string(self) -> None:
        """The reference dispatches on exact type, so every one of these raises there."""
        assert serialise({"x": Colour.RED}) == {"x": {"S": "RED"}}

    def test_an_integer_enum_is_stored_as_a_number(self) -> None:
        assert roundtrip(Size.LARGE) == 3

    def test_a_frozenset_is_stored_as_a_set(self) -> None:
        assert roundtrip(frozenset({"a", "b"})) == {"a", "b"}


class TestCollections:
    def test_a_list_survives(self) -> None:
        assert roundtrip(["a", 1, None]) == ["a", 1, None]

    def test_a_tuple_is_read_back_as_a_list(self) -> None:
        """DynamoDB has one sequence type, so the distinction cannot survive a round trip."""
        assert roundtrip(("a", "b")) == ["a", "b"]

    def test_a_mapping_survives(self) -> None:
        assert roundtrip({"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_a_string_set_survives(self) -> None:
        assert roundtrip({"a", "b"}) == {"a", "b"}

    def test_a_number_set_survives(self) -> None:
        assert roundtrip({1, 2}) == {1, 2}

    def test_a_binary_set_is_read_back_as_bytes(self) -> None:
        result = roundtrip({b"a", b"b"})
        assert result == {b"a", b"b"}
        assert all(isinstance(member, bytes) for member in result)

    def test_binary_nested_in_a_list_is_unwrapped(self) -> None:
        assert roundtrip([b"a"]) == [b"a"]
        assert isinstance(roundtrip([b"a"])[0], bytes)

    def test_binary_nested_in_a_map_is_unwrapped(self) -> None:
        assert isinstance(roundtrip({"blob": b"a"})["blob"], bytes)


class TestGuard:
    def test_an_empty_set_is_refused_before_the_request(self) -> None:
        """The service refuses it with a validation error naming nothing at all."""
        with pytest.raises(ItemEncodingError, match="empty set"):
            serialise({"tags": set()})

    def test_an_empty_list_is_allowed(self) -> None:
        """DynamoDB has an empty list and no empty set, so only one of the two is refused."""
        assert roundtrip([]) == []

    def test_an_empty_map_is_allowed(self) -> None:
        assert roundtrip({}) == {}

    def test_an_empty_string_is_allowed(self) -> None:
        """Refused by DynamoDB until 2020, and legal since. Refusing it here would be wrong."""
        assert roundtrip("") == ""

    @pytest.mark.parametrize(
        "value", [object(), complex(1, 2), range(3)], ids=["object", "complex", "range"]
    )
    def test_an_unstorable_type_is_refused_with_its_name(self, value: Any) -> None:
        with pytest.raises(ItemEncodingError, match=type(value).__name__):
            serialise({"x": value})

    def test_a_datetime_is_refused_rather_than_guessed(self) -> None:
        """There is no canonical stored form, so choosing one would be choosing wrong half the
        time — an epoch number and an ISO string sort differently and compare differently."""
        with pytest.raises(ItemEncodingError, match="datetime"):
            serialise({"at": datetime(2026, 8, 8, tzinfo=UTC)})

    def test_an_unstorable_type_inside_a_set_names_the_attribute(self) -> None:
        with pytest.raises(ItemEncodingError, match="tags"):
            serialise({"tags": {object()}})


class TestWholeItems:
    def test_an_item_is_converted_attribute_by_attribute(self) -> None:
        item = {"pk": "u#1", "count": 3, "active": True}
        assert serialise(item) == {
            "pk": {"S": "u#1"},
            "count": {"N": "3"},
            "active": {"BOOL": True},
        }

    def test_an_empty_item_is_empty(self) -> None:
        assert serialise({}) == {}
        assert deserialise({}) == {}


class TestNonFiniteNumbers:
    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_decimal_is_refused_locally(self, literal: str) -> None:
        """DynamoDB stores only finite numbers, and refuses these with an error naming the
        whole item rather than the attribute."""
        with pytest.raises(ItemEncodingError, match="finite"):
            serialise({"x": Decimal(literal)})
