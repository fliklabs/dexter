"""Tests for the dependency injection error hierarchy."""

import pytest

from dexter.commons import DexterError
from dexter.dependency_injection import DependencyInjectionError


class TestDependencyInjectionError:
    def test_descends_from_dexter_error(self) -> None:
        assert issubclass(DependencyInjectionError, DexterError)

    def test_is_caught_by_a_dexter_error_handler(self) -> None:
        with pytest.raises(DexterError):
            raise DependencyInjectionError("could not resolve")
