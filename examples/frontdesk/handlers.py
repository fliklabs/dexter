"""The handlers: API on one side, CQRS on the other.

Two kinds of handler live here and they are worth telling apart:

- An **API handler** takes a request model and returns a response model. It is the edge of the
  application. Several of these do nothing but translate — `BookRoomApi` turns an HTTP body
  into a command and the command's result into a receipt — and that thinness is the point: the
  rule about how a booking is made lives in the command handler, where a CLI or a scheduled job
  could reach it too.
- A **CQRS handler** takes a command, a query or an event. It never hears of HTTP.

Nothing here inherits from dexter. Each declares what it needs as annotated constructor
parameters and the container supplies them.
"""

from dexter.api import RequestContext
from dexter.cqrs import CommandBus, EventBus, QueryBus

from .domain import (
    BookingReceipt,
    Bookings,
    BookingView,
    BookRoom,
    BookRoomRequest,
    GetBooking,
    GetBookingRequest,
    RoomBooked,
    SearchRoomsRequest,
    Tenant,
    Whoami,
    WhoamiRequest,
)
from .services import Audit, Housekeeping

# ── the API edge ─────────────────────────────────────────────────────


class BookRoomApi:
    """Book a room."""

    def __init__(
        self,
        commands: CommandBus,
        context: RequestContext,
        tenant: Tenant,
        audit: Audit,
    ) -> None:
        """Take the bus, the request context, this request's tenant, and its audit note."""
        self.commands = commands
        self.context = context
        self.tenant = tenant
        self.audit = audit

    async def handle(self, request: BookRoomRequest) -> BookingReceipt:
        """Dispatch the command, then answer with where the booking can be read."""
        reference = await self.commands.dispatch(
            BookRoom(room=request.room, nights=request.nights, tenant=self.tenant.name)
        ).result()

        self.audit.note(f"booked {request.room}")
        self.context.set_header("location", f"/bookings/{reference}")
        return BookingReceipt(reference=reference, room=request.room)


class GetBookingApi:
    """Read one booking."""

    def __init__(self, queries: QueryBus) -> None:
        """Take the query bus."""
        self.queries = queries

    async def handle(self, request: GetBookingRequest) -> BookingView:
        """Ask for the booking and hand it straight back."""
        return await self.queries.ask(GetBooking(reference=request.reference))


class SearchRoomsApi:
    """List the rooms that are still free."""

    def __init__(self, bookings: Bookings, audit: Audit) -> None:
        """Take the book and this request's audit note."""
        self.bookings = bookings
        self.audit = audit

    async def handle(self, request: SearchRoomsRequest) -> list[str]:
        """Return the free rooms, narrowed by whatever was asked for."""
        self.audit.note("searched")
        return await self.bookings.free(request.floor, request.limit)


class WhoamiApi:
    """Report what the service can see about the caller."""

    def __init__(self, context: RequestContext, tenant: Tenant) -> None:
        """Take the request context and this request's tenant."""
        self.context = context
        self.tenant = tenant

    async def handle(self, request: WhoamiRequest) -> Whoami:  # noqa: ARG002
        """Read the caller's headers, cookie and address off the context."""
        return Whoami(
            tenant=self.tenant.name,
            session=self.context.cookies.get("session"),
            address=self.context.client_host,
            user_agent=self.context.headers.get("user-agent"),
        )


# ── the application core ─────────────────────────────────────────────


class BookRoomHandler:
    """Places the booking and announces it."""

    def __init__(self, bookings: Bookings, events: EventBus) -> None:
        """Take the book and the event bus."""
        self.bookings = bookings
        self.events = events

    async def handle(self, command: BookRoom) -> str:
        """Place the booking, publish `RoomBooked`, and return the reference."""
        reference = await self.bookings.place(
            command.room, command.nights, command.tenant
        )
        self.events.publish(RoomBooked(reference=reference, room=command.room))
        return reference


class GetBookingHandler:
    """Answers one booking."""

    def __init__(self, bookings: Bookings) -> None:
        """Take the book."""
        self.bookings = bookings

    async def handle(self, query: GetBooking) -> BookingView:
        """Read the booking, or fail if there is none."""
        return await self.bookings.read(query.reference)


class NotifyHousekeeping:
    """Reacts to a booking by putting the room on the housekeeping list."""

    def __init__(self, housekeeping: Housekeeping) -> None:
        """Take the housekeeping list."""
        self.housekeeping = housekeeping

    async def handle(self, event: RoomBooked) -> None:
        """Note that the room needs making up."""
        self.housekeeping.make_up(event.room)
