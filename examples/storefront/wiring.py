"""Where everything is registered. This is the file to read first.

The shape is the convention every dexter module follows:

- `use_cqrs(builder)` registers what the *module* provides — registries, pipeline, buses. It
  takes no configuration, and it must run before anything below it.
- `register_*(builder, ...)` registers what the *application* provides, once per handler, with
  `scope=` required on each.

Neither returns the builder: `ContainerBuilder` is not a chaining API.
"""

from dexter.cqrs import (
    register_command_handler,
    register_event_handler,
    register_middleware,
    register_query_handler,
    use_cqrs,
)
from dexter.dependency_injection import Container, ContainerBuilder, Scope

from .api import register_api
from .domain import CancelOrder, GetOrder, OrderBook, OrderPlaced, PlaceOrder
from .handlers import (
    CancelOrderHandler,
    ChargeCard,
    EmailCustomer,
    GetOrderHandler,
    PlaceOrderHandler,
    ReserveStock,
)
from .middleware import Correlate, Tracing
from .services import DispatchContext, InMemoryOrderBook, Warehouse


def build_container(
    *, with_failing_reaction: bool = False, with_api: bool = False
) -> Container:
    """Wire the storefront and return a container ready to resolve from.

    Args:
        with_failing_reaction: Whether to bind a third reaction to `OrderPlaced` that always
            fails. The failure walkthrough turns this on to show that the other two reactions
            still run and that the failures arrive together.
        with_api: Whether to add the HTTP edge in `api.py`. Off by default, so the walkthrough
            demonstrates CQRS on its own; `./dx serve` turns it on.
    """
    builder = ContainerBuilder()

    # Shared infrastructure. The order book stands in for a database, so it is a singleton.
    builder.register(OrderBook).to(InMemoryOrderBook, scope=Scope.SINGLETON)
    builder.register(Warehouse).to(Warehouse, scope=Scope.SINGLETON)

    # Per-request state, so Scoped. Every handler and middleware in one scope sees the same
    # one, and no scope can see another's.
    builder.register(DispatchContext).to(DispatchContext, scope=Scope.SCOPED)

    # Registers the three registries, the middleware pipeline, and the three buses. Every bus
    # is Scoped: it resolves handlers from the container it holds, so a singleton bus would
    # capture the root and bypass the scope it was asked for.
    use_cqrs(builder)

    # Registration order is nesting order, outermost first.
    register_middleware(builder, Tracing, scope=Scope.SCOPED)
    register_middleware(builder, Correlate, scope=Scope.SCOPED)

    # A handler holds no state between messages, so Transient: a fresh one per dispatch.
    register_command_handler(
        builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT
    )
    register_command_handler(
        builder, CancelOrder, CancelOrderHandler, scope=Scope.TRANSIENT
    )
    register_query_handler(builder, GetOrder, GetOrderHandler, scope=Scope.TRANSIENT)

    # Two reactions to one event. They run concurrently, in no guaranteed order.
    register_event_handler(builder, OrderPlaced, ReserveStock, scope=Scope.TRANSIENT)
    register_event_handler(builder, OrderPlaced, EmailCustomer, scope=Scope.TRANSIENT)

    if with_failing_reaction:
        register_event_handler(builder, OrderPlaced, ChargeCard, scope=Scope.TRANSIENT)

    if with_api:
        register_api(builder)

    return builder.build()
