"""Everything the orders module contributes. This is the file to read first.

The same shape as every other module: bind what it implements, register its handlers, map its
failures, expose its routes. It reads no differently for having a command, an event and a
dependency on another module — which is the point of the shape.
"""

from http import HTTPMethod, HTTPStatus

from dexter.api import HttpExposure, register_error, register_handler
from dexter.cqrs import (
    register_command_handler,
    register_event_handler,
    register_query_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .domain import (
    Dispatches,
    GetOrder,
    NoSuchOrderError,
    OrderPlaced,
    Orders,
    OutOfStockError,
    PlaceOrder,
)
from .handlers import (
    GetOrderApi,
    GetOrderHandler,
    PlaceOrderApi,
    PlaceOrderHandler,
    ScheduleDispatch,
)
from .services import DispatchQueue, InMemoryOrders

TAG = "orders"
"""Groups this module's routes in the generated schema."""


def use_orders(builder: ContainerBuilder) -> None:
    """Register orders: what it stores, what it does, and how it is reached."""
    builder.register(Orders).to(InMemoryOrders, scope=Scope.SINGLETON)
    builder.register(Dispatches).to(DispatchQueue, scope=Scope.SINGLETON)

    register_error(
        builder, NoSuchOrderError, status=HTTPStatus.NOT_FOUND, title="No such order"
    )
    register_error(
        builder, OutOfStockError, status=HTTPStatus.CONFLICT, title="Out of stock"
    )

    register_command_handler(
        builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT
    )
    register_query_handler(builder, GetOrder, GetOrderHandler, scope=Scope.TRANSIENT)

    # A reaction, not a step of placing the order. It runs after the command handler returns,
    # and leaving the scope is what waits for it.
    register_event_handler(
        builder, OrderPlaced, ScheduleDispatch, scope=Scope.TRANSIENT
    )

    register_handler(
        builder,
        PlaceOrderApi,
        HttpExposure(
            method=HTTPMethod.POST,
            path="/orders",
            status=HTTPStatus.CREATED,
            tags=(TAG,),
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        GetOrderApi,
        HttpExposure(method=HTTPMethod.GET, path="/orders/{reference}", tags=(TAG,)),
        scope=Scope.TRANSIENT,
    )
