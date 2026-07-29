"""A tiny application, local to these tests.

Two modules where one needs what the other provides, which is the only structural fact worth
having a fixture for: everything else about composition is one function calling another.
"""

from http import HTTPMethod, HTTPStatus

import pytest
from pydantic import BaseModel, ConfigDict

from dexter.api import HttpExposure, register_error, register_handler
from dexter.cqrs import Query, register_query_handler
from dexter.dependency_injection import ContainerBuilder, Scope


class Rates:
    """What the `rates` module provides, and what `billing` needs."""

    def __init__(self) -> None:
        self.pence = 250


class Bills:
    """What the `billing` module provides. Needs `Rates` from the other module."""

    def __init__(self, rates: Rates) -> None:
        self.rates = rates


class Unprovided:
    """Nothing registers this, so anything needing it fails at resolve."""


class Orphan:
    """Registered, but its own dependency is not."""

    def __init__(self, missing: Unprovided) -> None:
        self.missing = missing


class TooExpensiveError(Exception):
    """A domain failure, so a module has one to map."""


class GetBill(Query[int]):
    """Read what something costs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: int


class GetBillHandler:
    """Answers what something costs."""

    def __init__(self, bills: Bills) -> None:
        self.bills = bills

    async def handle(self, query: GetBill) -> int:
        return self.bills.rates.pence * query.quantity


class GetBillRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: int = 1


class GetBillApi:
    """Read what something costs."""

    def __init__(self, bills: Bills) -> None:
        self.bills = bills

    async def handle(self, request: GetBillRequest) -> int:
        return self.bills.rates.pence * request.quantity


def use_rates(builder: ContainerBuilder) -> None:
    """Everything the rates module contributes."""
    builder.register(Rates).to(Rates, scope=Scope.SINGLETON)


def use_billing(builder: ContainerBuilder) -> None:
    """Everything the billing module contributes. Needs `Rates` from another module."""
    builder.register(Bills).to(Bills, scope=Scope.SINGLETON)
    register_error(builder, TooExpensiveError, status=HTTPStatus.PAYMENT_REQUIRED)
    register_query_handler(builder, GetBill, GetBillHandler, scope=Scope.TRANSIENT)
    register_handler(
        builder,
        GetBillApi,
        HttpExposure(method=HTTPMethod.GET, path="/bill", tags=("billing",)),
        scope=Scope.TRANSIENT,
    )


def use_orphan(builder: ContainerBuilder) -> None:
    """A module whose dependency nobody provides."""
    builder.register(Orphan).to(Orphan, scope=Scope.SINGLETON)


@pytest.fixture
def builder() -> ContainerBuilder:
    """A bare builder, so a test can choose whether to wire the application."""
    return ContainerBuilder()
