"""Fixtures and sample services local to the dependency injection tests.

Anything defined here is visible only to tests in this directory and below, which is how each
module keeps its test support self-contained.
"""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

import pytest

from dexter.dependency_injection import ContainerBuilder


class Db:
    """A leaf dependency with no dependencies of its own."""

    def __init__(self) -> None:
        self.name = "db"


class Repository(ABC):
    """An abstract key, which is the common case for a registration."""

    @abstractmethod
    def find(self) -> str: ...


class SqlRepository(Repository):
    """A concrete implementation taking one dependency."""

    def __init__(self, db: Db) -> None:
        self.db = db

    def find(self) -> str:
        return f"row from {self.db.name}"


@runtime_checkable
class Greeter(Protocol):
    """A protocol key, to prove protocols work without any type-checker suppression."""

    def greet(self) -> str: ...


class Hello:
    """A greeter implementation."""

    def greet(self) -> str:
        return "hello"


class Handler:
    """A consumer two levels above the leaf."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository


@pytest.fixture
def builder() -> ContainerBuilder:
    """An empty builder."""
    return ContainerBuilder()
