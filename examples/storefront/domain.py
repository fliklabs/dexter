"""What the storefront is about, independent of how anything is implemented.

Everything here is either a message or an abstract contract. Note what the messages are *not*:
they carry no id, no timestamp and no correlation. Those belong to the act of sending, which
is the envelope's job — so two identical orders are equal values, and dispatching one twice
produces two distinguishable dispatches.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from dexter.cqrs import Command, Event, Query


class OrderId(BaseModel):
    """Identifies one order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str


class OrderSummary(BaseModel):
    """What a read of an order returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    sku: str
    quantity: int
    status: str


# ── commands ─────────────────────────────────────────────────────────


class PlaceOrder(Command[OrderId]):
    """Place an order, producing its id.

    The `[OrderId]` is what makes `await ticket.result()` return an `OrderId` rather than
    `Any`, with no cast at the call site.
    """

    sku: str
    quantity: int


class CancelOrder(Command[None]):
    """Cancel an order. `Command[None]` because there is nothing to return."""

    order_id: str


# ── queries ──────────────────────────────────────────────────────────


class GetOrder(Query[OrderSummary]):
    """Read one order."""

    order_id: str


# ── events ───────────────────────────────────────────────────────────


class OrderPlaced(Event):
    """An order was placed.

    Unparameterized: an event may have any number of handlers, so there is no single result to
    type — and none of them return anything.
    """

    order_id: str
    sku: str
    quantity: int


# ── contracts ────────────────────────────────────────────────────────


class OrderBook(ABC):
    """Where orders live. An abstract class, used directly as a container key."""

    @abstractmethod
    async def place(self, sku: str, quantity: int) -> OrderId:
        """Record a new order and return its id."""

    @abstractmethod
    async def cancel(self, order_id: str) -> None:
        """Mark an order cancelled."""

    @abstractmethod
    async def get(self, order_id: str) -> OrderSummary:
        """Return one order."""
