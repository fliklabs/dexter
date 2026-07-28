"""Implementations, and the per-request state a handler needs to correlate what it emits.

`DispatchContext` is the interesting one. A handler receives the *message*, not the envelope,
so on its own it has no way to say "the event I am publishing was caused by the command I am
handling". A scoped context that middleware fills in closes that gap, and it only works
because the context, the middleware and the buses all share one scope.
"""

import asyncio
import itertools
from typing import Any

from dexter.cqrs import Envelope

from .domain import OrderBook, OrderId, OrderSummary


class DispatchContext:
    """The envelope currently being handled, for whoever needs to correlate against it.

    Registered `Scope.SCOPED`, so there is exactly one per scope and every handler in that
    scope sees what the middleware put there. A singleton would share one request's envelope
    with every other request; a transient would hand each reader its own empty copy.
    """

    def __init__(self) -> None:
        """Start with nothing in flight."""
        self.current: Envelope[Any] | None = None


class InMemoryOrderBook(OrderBook):
    """An `OrderBook` holding orders in a dictionary.

    Registered `Scope.SINGLETON`, so every scope shares it — this stands in for a database,
    and a database does not restart per request.
    """

    def __init__(self) -> None:
        """Start with an empty book."""
        self._orders: dict[str, OrderSummary] = {}
        self._numbers = itertools.count(1)

    async def place(self, sku: str, quantity: int) -> OrderId:
        """Record a new order and return its id."""
        await asyncio.sleep(0)
        order_id = f"ord-{next(self._numbers):03d}"
        self._orders[order_id] = OrderSummary(
            id=order_id, sku=sku, quantity=quantity, status="placed"
        )
        return OrderId(value=order_id)

    async def cancel(self, order_id: str) -> None:
        """Mark an order cancelled."""
        await asyncio.sleep(0)
        existing = self._orders[order_id]
        self._orders[order_id] = existing.model_copy(update={"status": "cancelled"})

    async def get(self, order_id: str) -> OrderSummary:
        """Return one order."""
        await asyncio.sleep(0)
        return self._orders[order_id]


class Warehouse:
    """Stands in for a downstream system, recording what it was told to do."""

    def __init__(self) -> None:
        """Start with nothing reserved."""
        self.reserved: list[str] = []
