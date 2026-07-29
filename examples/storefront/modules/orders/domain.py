"""What an order is, and what placing one means.

The messages carry no id, no timestamp and no correlation. Those belong to the act of sending,
which is the envelope's job — so two identical orders are equal values, and placing the same
one twice produces two distinguishable dispatches rather than one ambiguous record.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from dexter.cqrs import Command, Event, Query


class Order(BaseModel):
    """One placed order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    sku: str
    quantity: int
    pence: int
    """What it came to, priced when it was placed rather than read back later."""

    status: str


class NoSuchOrderError(Exception):
    """No order has that reference."""


class OutOfStockError(Exception):
    """The catalogue does not have enough of it."""


# ── what the module does ─────────────────────────────────────────────


class PlaceOrder(Command[str]):
    """Place an order, producing its reference.

    `Command[str]` is what makes `await ticket.result()` a `str` at the call site rather than
    `Any`, with no cast.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    quantity: int = Field(gt=0, le=50)


class GetOrder(Query[Order]):
    """Read one order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str


class OrderPlaced(Event):
    """An order was placed.

    Unparameterized: an event may have any number of reactions, so there is no single result
    to type, and none of them return anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    sku: str
    quantity: int


# ── what the module depends on ───────────────────────────────────────


class Orders(ABC):
    """Where orders live."""

    @abstractmethod
    async def place(self, sku: str, quantity: int, pence: int) -> str:
        """Record an order and return its reference."""

    @abstractmethod
    async def get(self, reference: str) -> Order:
        """Return one order, or raise `NoSuchOrderError`."""


class Dispatches(ABC):
    """Whatever gets orders out of the door.

    A second contract so the module has something to react to `OrderPlaced` *with* — the point
    being that the reaction is not the command handler, and does not run in its call.
    """

    @abstractmethod
    async def schedule(self, reference: str, sku: str, quantity: int) -> None:
        """Queue an order for dispatch."""

    @abstractmethod
    def pending(self) -> tuple[str, ...]:
        """Everything queued so far, oldest first."""
