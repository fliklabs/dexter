"""Tests for the root of dexter's exception hierarchy."""

import pytest

from dexter.commons import DexterError


class TestDexterError:
    def test_can_be_raised_and_caught_with_a_message(self) -> None:
        with pytest.raises(DexterError, match="something went wrong"):
            raise DexterError("something went wrong")

    def test_preserves_its_message(self) -> None:
        error = DexterError("boom")
        assert str(error) == "boom"
