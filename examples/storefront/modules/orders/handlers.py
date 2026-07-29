"""The orders module's handlers.

`PlaceOrderHandler` is the one worth reading. It asks for a `Catalogue` — the *contract*
declared by the catalogue module — and receives whatever the application bound to it. That is
the whole of how these two modules relate: no import of the other module's services, no
registry of modules consulted, no ordering to get right. Delete `use_catalogue` from the
application and this fails when the container resolves it, naming what was missing.
"""

from pydantic import BaseModel, ConfigDict, Field

from dexter.cqrs import CommandBus, EventBus, QueryBus

from ..catalogue.domain import Catalogue
from .domain import (
    Dispatches,
    GetOrder,
    Order,
    OrderPlaced,
    Orders,
    OutOfStockError,
    PlaceOrder,
)

# ── the application core ─────────────────────────────────────────────


class PlaceOrderHandler:
    """Prices an order against the catalogue, records it, and announces it."""

    def __init__(self, catalogue: Catalogue, orders: Orders, events: EventBus) -> None:
        """Take another module's contract, this module's store, and the event bus."""
        self.catalogue = catalogue
        self.orders = orders
        self.events = events

    async def handle(self, command: PlaceOrder) -> str:
        """Place the order, publish `OrderPlaced`, and return the reference."""
        product = await self.catalogue.get(command.sku)
        if product.in_stock < command.quantity:
            raise OutOfStockError(
                f"{command.sku} has {product.in_stock} left, not {command.quantity}"
            )

        await self.catalogue.take(command.sku, command.quantity)
        reference = await self.orders.place(
            command.sku, command.quantity, product.pence * command.quantity
        )

        # Published, not awaited. What reacts to it is nobody's business here, and leaving the
        # request's scope is what waits for those reactions.
        self.events.publish(
            OrderPlaced(reference=reference, sku=command.sku, quantity=command.quantity)
        )
        return reference


class GetOrderHandler:
    """Answers one order."""

    def __init__(self, orders: Orders) -> None:
        """Take the order book."""
        self.orders = orders

    async def handle(self, query: GetOrder) -> Order:
        """Read the order, or fail if there is none."""
        return await self.orders.get(query.reference)


class ScheduleDispatch:
    """Reacts to a placed order by queueing it to be sent."""

    def __init__(self, dispatches: Dispatches) -> None:
        """Take the dispatch queue."""
        self.dispatches = dispatches

    async def handle(self, event: OrderPlaced) -> None:
        """Queue the order."""
        await self.dispatches.schedule(event.reference, event.sku, event.quantity)


# ── the API edge ─────────────────────────────────────────────────────


class PlaceOrderRequest(BaseModel):
    """The body of an order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="What to order, such as DX-100.")
    quantity: int = Field(gt=0, le=50, description="How many, up to fifty.")


class GetOrderRequest(BaseModel):
    """Read one order. `reference` is read from the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str = Field(description="The reference returned when it was placed.")


class PlaceOrderApi:
    """Place an order."""

    def __init__(self, commands: CommandBus, queries: QueryBus) -> None:
        """Take both buses: one to place the order, one to read it back."""
        self.commands = commands
        self.queries = queries

    async def handle(self, request: PlaceOrderRequest) -> Order:
        """Place the order and answer with it as stored."""
        reference = await self.commands.dispatch(
            PlaceOrder(sku=request.sku, quantity=request.quantity)
        ).result()
        return await self.queries.ask(GetOrder(reference=reference))


class GetOrderApi:
    """Read one order."""

    def __init__(self, queries: QueryBus) -> None:
        """Take the query bus."""
        self.queries = queries

    async def handle(self, request: GetOrderRequest) -> Order:
        """Ask for the order and hand it straight back."""
        return await self.queries.ask(GetOrder(reference=request.reference))
