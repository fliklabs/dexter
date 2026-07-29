"""An HTTP edge over the same buses the walkthrough dispatches on.

Every handler here is a translator and nothing more: it turns a request into a command or a
query, hands it to a bus, and shapes the result. The rule about what placing an order *means*
stays in `PlaceOrderHandler`, where the walkthrough, a scheduled job and this endpoint can all
reach it.

That thinness is the point, and it is why the endpoints below carry no logic worth testing.
The interesting behaviour is what the container does around them: leaving the request scope
settles the buses, so by the time a caller reads the response to `POST /orders`, the event that
command published has already reached both reactions.
"""

from http import HTTPMethod, HTTPStatus

from pydantic import BaseModel, ConfigDict, Field

from dexter.api import HttpExposure, register_error, register_handler, use_api
from dexter.cqrs import CommandBus, QueryBus
from dexter.dependency_injection import ContainerBuilder, Scope

from .domain import CancelOrder, GetOrder, OrderSummary, PlaceOrder
from .services import Warehouse


class PlaceOrderRequest(BaseModel):
    """The body of an order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="What is being ordered.")
    quantity: int = Field(gt=0, le=100, description="How many, up to a hundred.")


class OrderReceipt(BaseModel):
    """What a caller gets back when an order is placed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    reserved: list[str]
    """What the warehouse had reserved by the time this response was built.

    Populated deliberately: the command's event reached `ReserveStock` before the request
    scope closed, which is the whole claim `dexter.api` and `dexter.cqrs` make together.
    """


class OrderRef(BaseModel):
    """Names one order. `order_id` is read from the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(description="The order's id, as returned when it was placed.")


class PlaceOrderApi:
    """Place an order."""

    def __init__(self, commands: CommandBus, warehouse: Warehouse) -> None:
        """Take the command bus and the warehouse the event's reaction writes into."""
        self.commands = commands
        self.warehouse = warehouse

    async def handle(self, request: PlaceOrderRequest) -> OrderReceipt:
        """Dispatch the command, redeem its ticket, and report what happened."""
        order_id = await self.commands.dispatch(
            PlaceOrder(sku=request.sku, quantity=request.quantity)
        ).result()
        return OrderReceipt(
            order_id=order_id.value, reserved=list(self.warehouse.reserved)
        )


class GetOrderApi:
    """Read one order."""

    def __init__(self, queries: QueryBus) -> None:
        """Take the query bus."""
        self.queries = queries

    async def handle(self, request: OrderRef) -> OrderSummary:
        """Ask for the order and hand it straight back."""
        return await self.queries.ask(GetOrder(order_id=request.order_id))


class CancelOrderApi:
    """Cancel one order."""

    def __init__(self, commands: CommandBus) -> None:
        """Take the command bus."""
        self.commands = commands

    async def handle(self, request: OrderRef) -> None:
        """Dispatch the cancellation and wait for it, so a failure is reported."""
        await self.commands.dispatch(CancelOrder(order_id=request.order_id)).result()


def register_api(builder: ContainerBuilder) -> None:
    """Add the HTTP edge to a builder the rest of the storefront has already been wired into."""
    use_api(builder)

    # The order book is a plain dictionary, so an unknown id surfaces as `KeyError`. Mapping an
    # exception you did not define is the ordinary case, not a special one — `register_error`
    # takes any exception class, and the MRO walk means a base would cover it just as well.
    register_error(
        builder, KeyError, status=HTTPStatus.NOT_FOUND, title="No such order"
    )

    register_handler(
        builder,
        PlaceOrderApi,
        HttpExposure(
            method=HTTPMethod.POST,
            path="/orders",
            status=HTTPStatus.CREATED,
            tags=("storefront",),
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        GetOrderApi,
        HttpExposure(
            method=HTTPMethod.GET, path="/orders/{order_id}", tags=("storefront",)
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        CancelOrderApi,
        HttpExposure(
            method=HTTPMethod.DELETE,
            path="/orders/{order_id}",
            status=HTTPStatus.NO_CONTENT,
            tags=("storefront",),
        ),
        scope=Scope.TRANSIENT,
    )
