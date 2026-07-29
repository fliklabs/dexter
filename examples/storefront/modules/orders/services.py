"""The orders module's implementations."""

import asyncio
import itertools

from .domain import Dispatches, NoSuchOrderError, Order, Orders


class InMemoryOrders(Orders):
    """An `Orders` holding everything in a dictionary. A singleton, like a database."""

    def __init__(self) -> None:
        """Start with an empty book."""
        self._orders: dict[str, Order] = {}
        self._numbers = itertools.count(1)

    async def place(self, sku: str, quantity: int, pence: int) -> str:
        """Record an order and return its reference."""
        await asyncio.sleep(0)
        reference = f"ORD-{next(self._numbers):04d}"
        self._orders[reference] = Order(
            reference=reference,
            sku=sku,
            quantity=quantity,
            pence=pence,
            status="placed",
        )
        return reference

    async def get(self, reference: str) -> Order:
        """Return one order, or say which one was asked for."""
        await asyncio.sleep(0)
        order = self._orders.get(reference)
        if order is None:
            raise NoSuchOrderError(f"no order {reference}")
        return order


class DispatchQueue(Dispatches):
    """Records what it was told to send. A singleton, standing in for a real queue."""

    def __init__(self) -> None:
        """Start with nothing queued."""
        self.queued: list[str] = []

    async def schedule(self, reference: str, sku: str, quantity: int) -> None:
        """Queue an order for dispatch."""
        await asyncio.sleep(0)
        self.queued.append(f"{reference}: {quantity} x {sku}")

    def pending(self) -> tuple[str, ...]:
        """Everything queued so far, oldest first."""
        return tuple(self.queued)
