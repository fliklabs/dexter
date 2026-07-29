"""The types this service is made of, and the contracts it depends on.

Three kinds live here, and keeping them in one file is what makes the dependency direction
obvious: the CQRS messages the application sends itself, the request and response models its
API speaks, and the abstract contracts its handlers are written against.

Nothing here knows how anything works. `BookRoom` does not know a bus exists; `BookRoomRequest`
does not know HTTP does.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from dexter.cqrs import Command, Event, Query


class Tenant:
    """Who is asking. Built per request from a header, and injected wherever it is needed.

    A plain class rather than a string so that a service asking for one cannot be handed any
    old text by accident, and so that nothing downstream has to know it came from a header.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        """Record the tenant's name."""
        self.name = name

    def __repr__(self) -> str:
        return f"Tenant({self.name!r})"


class BookingView(BaseModel):
    """One booking, as anyone reading it sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    room: str
    nights: int
    tenant: str
    status: str


# ── what the application sends itself ────────────────────────────────


class BookRoom(Command[str]):
    """Take a room off the board and return the new booking's reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    room: str
    nights: int
    tenant: str


class GetBooking(Query[BookingView]):
    """Read one booking back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str


class RoomBooked(Event):
    """A room has been booked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    room: str


# ── what the API speaks ──────────────────────────────────────────────


class BookRoomRequest(BaseModel):
    """The body of a booking request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    room: str
    nights: int = Field(gt=0, le=30, description="How many nights, up to thirty.")


class BookingReceipt(BaseModel):
    """What a caller gets back when a booking is made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    room: str


class GetBookingRequest(BaseModel):
    """Read one booking. `reference` is read from the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str = Field(description="The booking's reference.")


class SearchRoomsRequest(BaseModel):
    """Look for free rooms. Every field is read from the query string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    floor: int | None = Field(default=None, description="Restrict to one floor.")
    limit: int = Field(default=3, ge=1, le=20, description="How many to return.")


class WhoamiRequest(BaseModel):
    """Takes nothing. Everything it reports comes from the request context."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Whoami(BaseModel):
    """What the service can see about the caller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    session: str | None
    address: str | None
    user_agent: str | None


# ── contracts ────────────────────────────────────────────────────────


class NotAuthenticatedError(Exception):
    """The caller did not say who they are."""


class NoSuchBookingError(Exception):
    """No booking has that reference."""


class RoomTakenError(Exception):
    """The room is already booked."""


class Bookings(ABC):
    """Where bookings are kept. An ABC used directly as a container key."""

    @abstractmethod
    async def place(self, room: str, nights: int, tenant: str) -> str:
        """Record a booking and return its reference."""

    @abstractmethod
    async def read(self, reference: str) -> BookingView:
        """Return one booking, or raise `NoSuchBookingError`."""

    @abstractmethod
    async def free(self, floor: int | None, limit: int) -> list[str]:
        """Return the rooms that are still free."""
