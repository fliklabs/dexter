"""What taskflow is about, independent of how anything is implemented.

Everything here is either data or an abstract contract. These are the types used as container
*keys*, which is why none of them mention dexter: a domain should not know it is being
injected.

Note that `Repository` is an abstract base class and `Clock` and `Notifier` are protocols. All
three work as container keys with no type-checker suppression at the call site.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    """Application configuration, built before the container and bound into it.

    A frozen pydantic model, which is the shape dexter recommends for anything crossing from
    the outside world into the application.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dsn: str
    worker_count: int


class Job(BaseModel):
    """A unit of work handed to the service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    payload: str


class JobResult(BaseModel):
    """The outcome of handling a job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    handled_at: datetime
    notified: bool


@runtime_checkable
class Clock(Protocol):
    """Supplies the current time.

    A protocol rather than a class so tests can substitute a fixed clock without inheriting
    anything.
    """

    def now(self) -> datetime:
        """Return the current time."""
        ...


@runtime_checkable
class Notifier(Protocol):
    """Announces that a job was handled.

    Bound optionally in `wiring.py`: a handler declares `Notifier | None` and works either
    way, which is how dexter models a subsystem that may not be configured.
    """

    def notify(self, message: str) -> None:
        """Announce `message`."""
        ...


class Repository(ABC):
    """Stores and retrieves job results.

    An abstract base class, to show that an ABC is as usable a key as a protocol.
    """

    @abstractmethod
    async def save(self, result: JobResult) -> None:
        """Persist `result`."""

    @abstractmethod
    async def count(self) -> int:
        """Return how many results this repository holds."""


class ArchiveStore(ABC):
    """A subsystem deliberately left unregistered, to show what a failure looks like."""

    @abstractmethod
    async def archive(self, job_id: str) -> None:
        """Move `job_id` to cold storage."""
