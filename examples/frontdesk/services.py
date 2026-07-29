"""The things that do the work, and the one factory worth reading twice.

`current_tenant` is the piece that matters. It is an ordinary function taking a
`RequestContext` and returning a `Tenant`, bound `Scope.SCOPED` — so anything in the graph can
declare `tenant: Tenant` and receive the right one for the request being served, without ever
mentioning a header, a request, or HTTP.

That is the alternative to reaching for an ambient global, and it is worth being explicit about
why the obvious shortcut is wrong. The shortcut is to stash the caller somewhere process-wide
and read it back where it is needed. Under `asyncio` that is a bug rather than a shortcut:
concurrent requests share a thread, so one caller's identity is visible to another's request.
Here the container holds it instead, one per scope, and a scope is one request.
"""

import itertools

from dexter.api import RequestContext

from .domain import Bookings, BookingView, NoSuchBookingError, RoomTakenError, Tenant


def current_tenant(context: RequestContext) -> Tenant:
    """The tenant this request is for, read from a header.

    Bound `Scope.SCOPED`, so it is built once per request and shared by everything in it. A
    singleton would be refused when the container is built, because it would capture one
    request's tenant and serve it to every other one — `CaptiveDependencyError` says so by
    name.
    """
    return Tenant(context.headers.get("x-tenant") or "walk-in")


class Housekeeping:
    """Records the rooms it has been told to make up. A singleton, like a database."""

    def __init__(self) -> None:
        """Start with nothing to do."""
        self.pending: list[str] = []

    def make_up(self, room: str) -> None:
        """Note that a room needs making up."""
        self.pending.append(room)


class InMemoryBookings(Bookings):
    """A `Bookings` holding everything in a dictionary.

    Registered `Scope.SINGLETON`, so every request shares it — this stands in for a database,
    and a database does not restart per request.
    """

    ROOMS = ("101", "102", "201", "202", "301")

    def __init__(self) -> None:
        """Start with an empty book and every room free."""
        self._bookings: dict[str, BookingView] = {}
        self._taken: set[str] = set()
        self._numbers = itertools.count(1)

    async def place(self, room: str, nights: int, tenant: str) -> str:
        """Record a booking and return its reference."""
        if room in self._taken:
            raise RoomTakenError(f"room {room} is already booked")
        if room not in self.ROOMS:
            raise NoSuchBookingError(f"there is no room {room}")

        reference = f"BK-{next(self._numbers):03d}"
        self._taken.add(room)
        self._bookings[reference] = BookingView(
            reference=reference,
            room=room,
            nights=nights,
            tenant=tenant,
            status="confirmed",
        )
        return reference

    async def read(self, reference: str) -> BookingView:
        """Return one booking, or raise `NoSuchBookingError`."""
        booking = self._bookings.get(reference)
        if booking is None:
            raise NoSuchBookingError(f"no booking {reference}")
        return booking

    async def free(self, floor: int | None, limit: int) -> list[str]:
        """Return the rooms that are still free."""
        rooms = [room for room in self.ROOMS if room not in self._taken]
        if floor is not None:
            rooms = [room for room in rooms if room.startswith(str(floor))]
        return rooms[:limit]


class AuditTrail:
    """Where audit notes end up. A singleton."""

    def __init__(self) -> None:
        """Start with an empty trail."""
        self.entries: list[str] = []

    def record(self, entry: str) -> None:
        """Record one line."""
        self.entries.append(entry)


class Audit:
    """A per-request note of who did what, flushed as the request scope closes.

    Bound with `dispose=Audit.flush`, so the container writes it out on the way out of the
    scope — before the caller is told anything. Nothing in a handler has to remember to do it.

    Defined after `AuditTrail` because its constructor names it: this repository does not use
    `from __future__ import annotations`, so an annotation is a real object evaluated here.
    """

    def __init__(self, tenant: Tenant, trail: AuditTrail) -> None:
        """Take the request's tenant and the process-wide trail to write into."""
        self.tenant = tenant
        self.trail = trail
        self.entries: list[str] = []

    def note(self, what: str) -> None:
        """Record something this request did."""
        self.entries.append(what)

    async def flush(self) -> None:
        """Write everything this request did into the trail."""
        for entry in self.entries:
            self.trail.record(f"{self.tenant.name}: {entry}")
