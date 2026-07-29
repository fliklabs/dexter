"""Where everything is registered. This is the file to read first.

Three modules are wired together here, and the shape is the same for each: `use_*` registers
what the *module* provides and takes no configuration, then one `register_*` per thing the
*application* contributes, with `scope=` required wherever something is constructed.

The interesting part is how little joins them. `dexter.api` does not import `dexter.cqrs` and
knows nothing about buses; `dexter.cqrs` has never heard of a request. What connects them is
the container: an API handler asks for a `CommandBus`, and the scope the request opened is the
scope the bus resolves its handlers from. Leaving that scope settles the bus, which is why the
booking's event has reached housekeeping before the caller reads the response.
"""

from http import HTTPMethod, HTTPStatus

from dexter.api import (
    HttpExposure,
    register_api_middleware,
    register_error,
    register_handler,
    use_api,
)
from dexter.cqrs import (
    register_command_handler,
    register_event_handler,
    register_query_handler,
    use_cqrs,
)
from dexter.dependency_injection import Container, ContainerBuilder, Scope

from .domain import (
    Bookings,
    BookRoom,
    GetBooking,
    NoSuchBookingError,
    NotAuthenticatedError,
    RoomBooked,
    RoomTakenError,
    Tenant,
)
from .handlers import (
    BookRoomApi,
    BookRoomHandler,
    GetBookingApi,
    GetBookingHandler,
    NotifyHousekeeping,
    SearchRoomsApi,
    WhoamiApi,
)
from .middleware import RequireTenant, Trace
from .services import Audit, AuditTrail, Housekeeping, InMemoryBookings, current_tenant


def build_container() -> Container:
    """Wire the front desk and return a container ready to serve from."""
    builder = ContainerBuilder()

    # Shared infrastructure. These stand in for a database, so they are singletons.
    builder.register(Bookings).to(InMemoryBookings, scope=Scope.SINGLETON)
    builder.register(Housekeeping).to(Housekeeping, scope=Scope.SINGLETON)
    builder.register(AuditTrail).to(AuditTrail, scope=Scope.SINGLETON)

    # Per-request, so Scoped. `current_tenant` is an ordinary function taking the request
    # context — which is what lets a service ask for a `Tenant` and never mention HTTP.
    builder.register(Tenant).to(current_tenant, scope=Scope.SCOPED)

    # Written out by the container as the request scope closes, so no handler has to remember.
    builder.register(Audit).to(Audit, scope=Scope.SCOPED, dispose=Audit.flush)

    # Registers the three registries, the middleware pipeline, and the three buses.
    use_cqrs(builder)

    # Registers the exposure registry, the API pipeline, the error map, and `RequestContext`.
    use_api(builder)

    # Registration order is nesting order, outermost first.
    register_api_middleware(builder, Trace, scope=Scope.SCOPED)
    register_api_middleware(builder, RequireTenant, scope=Scope.SCOPED)

    # A domain failure, and what it means to a caller. No `scope=`: nothing is constructed.
    register_error(
        builder,
        NoSuchBookingError,
        status=HTTPStatus.NOT_FOUND,
        title="No such booking",
    )
    register_error(
        builder, RoomTakenError, status=HTTPStatus.CONFLICT, title="Room already booked"
    )
    # Raised by `RequireTenant`, which guards every route. A refusal has to be expressible
    # without knowing what the route it refused would have returned, and a mapped exception is.
    register_error(
        builder,
        NotAuthenticatedError,
        status=HTTPStatus.UNAUTHORIZED,
        title="Who are you?",
    )

    # The API edge. A handler holds no state between requests, so Transient.
    register_handler(
        builder,
        SearchRoomsApi,
        HttpExposure(method=HTTPMethod.GET, path="/rooms", tags=("rooms",)),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        WhoamiApi,
        HttpExposure(method=HTTPMethod.GET, path="/whoami", tags=("session",)),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        BookRoomApi,
        HttpExposure(
            method=HTTPMethod.POST,
            path="/bookings",
            status=HTTPStatus.CREATED,
            tags=("bookings",),
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        GetBookingApi,
        HttpExposure(
            method=HTTPMethod.GET, path="/bookings/{reference}", tags=("bookings",)
        ),
        scope=Scope.TRANSIENT,
    )

    # The application core, reachable from anywhere a bus is — not only from HTTP.
    register_command_handler(builder, BookRoom, BookRoomHandler, scope=Scope.TRANSIENT)
    register_query_handler(
        builder, GetBooking, GetBookingHandler, scope=Scope.TRANSIENT
    )
    register_event_handler(
        builder, RoomBooked, NotifyHousekeeping, scope=Scope.TRANSIENT
    )

    return builder.build()
