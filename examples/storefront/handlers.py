"""The handlers. Each is an ordinary class with one async `handle` method.

Nothing here inherits from dexter. A handler declares what it needs as annotated constructor
parameters and the container supplies them, exactly as it would for any other class — so a
handler is testable by constructing it directly with fakes, with no bus and no container.
"""

from dexter.cqrs import EventBus

from .domain import (
    CancelOrder,
    GetOrder,
    OrderBook,
    OrderId,
    OrderPlaced,
    OrderSummary,
    PlaceOrder,
)
from .services import DispatchContext, Warehouse


class PlaceOrderHandler:
    """Places an order and announces it.

    Takes the `EventBus` as an ordinary dependency, and reads `DispatchContext` so the event
    it publishes is stamped as *caused by* the command being handled. Both come from the same
    scope this handler was resolved from.
    """

    def __init__(
        self, orders: OrderBook, events: EventBus, context: DispatchContext
    ) -> None:
        """Take the order book, the event bus, and the current dispatch context."""
        self.orders = orders
        self.events = events
        self.context = context

    async def handle(self, command: PlaceOrder) -> OrderId:
        """Place the order, publish `OrderPlaced`, and return the new id."""
        order_id = await self.orders.place(command.sku, command.quantity)
        self.events.publish(
            OrderPlaced(
                order_id=order_id.value, sku=command.sku, quantity=command.quantity
            ),
            caused_by=self.context.current,
        )
        return order_id


class CancelOrderHandler:
    """Cancels an order and returns nothing, because `CancelOrder` is a `Command[None]`."""

    def __init__(self, orders: OrderBook) -> None:
        """Take the order book."""
        self.orders = orders

    async def handle(self, command: CancelOrder) -> None:
        """Mark the order cancelled."""
        await self.orders.cancel(command.order_id)


class GetOrderHandler:
    """Answers a read. No ticket: `await queries.ask(...)` returns the summary directly."""

    def __init__(self, orders: OrderBook) -> None:
        """Take the order book."""
        self.orders = orders

    async def handle(self, query: GetOrder) -> OrderSummary:
        """Return the order."""
        return await self.orders.get(query.order_id)


class ReserveStock:
    """One of two reactions to `OrderPlaced`. Returns `None`, as every event handler must."""

    def __init__(self, warehouse: Warehouse) -> None:
        """Take the warehouse this reaction talks to."""
        self.warehouse = warehouse

    async def handle(self, event: OrderPlaced) -> None:
        """Reserve stock for the order."""
        self.warehouse.reserved.append(f"{event.sku} x{event.quantity}")


class EmailCustomer:
    """The other reaction. It runs concurrently with `ReserveStock`, not after it."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[str] = []

    async def handle(self, event: OrderPlaced) -> None:
        """Send the confirmation."""
        self.sent.append(event.order_id)


class ChargeCard:
    """A reaction that fails, so the transcript can show what happens to the others.

    Bound only by the failure walkthrough. Its failure does not stop `ReserveStock` or
    `EmailCustomer` from running.
    """

    async def handle(self, event: OrderPlaced) -> None:
        """Fail, as the payment gateway is pretending to be down."""
        raise ConnectionError(f"payment gateway refused order {event.order_id}")
