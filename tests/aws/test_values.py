"""Covers `dexter.aws.values`: the contract, and the implementation that reaches nothing.

`StaticValue` is small and the tests are short, but one of them is load-bearing: a value holder
whose `repr` discloses what it holds puts a password into the first log line written by anything
that happens to print it.
"""

from typing import Any, Protocol

import pytest

from dexter.aws import StaticValue, ValueSource


class DatabasePassword(ValueSource, Protocol):
    """A marker of the kind an application declares."""


class Counting:
    """A `ValueSource` written by hand, to show what conforming actually costs."""

    def __init__(self) -> None:
        self.reads = 0

    async def value(self) -> str:
        self.reads += 1
        return "counted"


class TestStaticValue:
    async def test_returns_what_it_was_given(self) -> None:
        assert await StaticValue("hunter2").value() == "hunter2"

    async def test_returns_the_same_value_every_time(self) -> None:
        static = StaticValue("hunter2")
        assert await static.value() == await static.value()

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_rejects_a_blank_value(self, blank: str) -> None:
        """A component built with an empty key fails much later, as somebody else's 401."""
        with pytest.raises(ValueError, match="must not be empty"):
            StaticValue(blank)

    def test_repr_discloses_nothing(self) -> None:
        """**The one property this class exists to have.**

        A frozen pydantic model would print its fields here *and* inside the `ValidationError`
        raised by an unrelated field of any model holding it, which is how a secret reaches a log
        the first time something else is wrong.
        """
        assert repr(StaticValue("hunter2")) == "StaticValue(...)"
        assert "hunter2" not in repr(StaticValue("hunter2"))

    def test_it_is_slotted_so_nothing_can_be_stuck_to_it(self) -> None:
        static: Any = StaticValue("hunter2")
        with pytest.raises(AttributeError):
            static.leaked = "hunter2"


class TestTheContract:
    async def test_anything_with_the_method_satisfies_it(self) -> None:
        """No base class, no registration, no import — which is why a test double is four lines."""
        counting = Counting()
        source: ValueSource = counting

        assert await source.value() == "counted"
        assert counting.reads == 1

    async def test_a_static_value_satisfies_an_application_s_marker(self) -> None:
        """The substitution in one line, with no AWS anywhere near it."""
        password: DatabasePassword = StaticValue("hunter2")
        assert await password.value() == "hunter2"
