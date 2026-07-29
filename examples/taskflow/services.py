"""Implementations of the domain contracts, plus the things that do the work.

Every constructor here takes its dependencies as annotated parameters and does nothing else.
That is the whole contract with the container: annotate what you need, and it arrives.
"""

import asyncio
import itertools
from datetime import UTC, datetime

from dexter.dependency_injection import Container

from .domain import (
    ArchiveStore,
    Clock,
    Job,
    JobResult,
    Notifier,
    Repository,
    Settings,
)


class ConnectionPool:
    """A connection pool that has to be opened before use.

    Bound as a `Scope.SINGLETON` through an async factory, because opening it is awaitable and
    a constructor cannot be. One of these should exist per process.
    """

    def __init__(self, dsn: str) -> None:
        """Record the DSN; the pool is not usable until `open` has been awaited."""
        self.dsn = dsn
        self.is_open = False

    async def open(self) -> None:
        """Establish the pool. Stands in for real network setup."""
        await asyncio.sleep(0)
        self.is_open = True

    async def aclose(self) -> None:
        """Release the pool. Registered as this binding's `dispose=`.

        A singleton is owned by the root container, so this runs when the container closes —
        not when any individual scope does.
        """
        await asyncio.sleep(0)
        self.is_open = False


async def open_pool(settings: Settings) -> ConnectionPool:
    """Build and open a pool.

    An async factory. `settings` is injected exactly as it would be into a constructor, so
    nothing is lost by using a factory instead of a class.
    """
    pool = ConnectionPool(settings.dsn)
    await pool.open()
    return pool


class SystemClock:
    """A `Clock` backed by the wall clock."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


class ConsoleNotifier:
    """A `Notifier` that records what it was told, so the demo can show it was used."""

    def __init__(self) -> None:
        """Start with nothing announced."""
        self.messages: list[str] = []

    def notify(self, message: str) -> None:
        """Record `message`."""
        self.messages.append(message)


class InMemoryRepository(Repository):
    """A `Repository` holding results in memory.

    Bound as `Scope.SCOPED`, so each scope gets its own — which is what you want for anything
    holding per-request state, such as a transaction or a unit of work.
    """

    def __init__(self, pool: ConnectionPool, clock: Clock) -> None:
        """Take the shared pool and the clock; both arrive from the container."""
        self.pool = pool
        self.clock = clock
        self._results: list[JobResult] = []

    async def save(self, result: JobResult) -> None:
        """Persist `result` in this scope's store."""
        await asyncio.sleep(0)
        self._results.append(result)

    async def count(self) -> int:
        """Return how many results this instance holds."""
        return len(self._results)


class UnitOfWork:
    """Per-request work that has to be finished off, whatever happens.

    Bound `Scope.SCOPED` with a `dispose=`, which is the pairing that makes disposal worth
    having: one of these exists per scope, and leaving the scope releases it. Note the key is
    the concrete class, so the `dispose=` callback is checked against it — a callback is typed
    against the *key* being bound, not against whatever implements it.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        """Take the shared pool this unit of work runs against."""
        self.pool = pool
        self.is_closed = False

    async def aclose(self) -> None:
        """Finish the unit of work. Runs when the owning scope exits."""
        await asyncio.sleep(0)
        self.is_closed = True


_request_numbers = itertools.count(1)


class RequestContext:
    """Per-resolution correlation state.

    Bound as `Scope.TRANSIENT`, so every resolution produces a new one. Useful for anything
    that must never be shared, and the clearest way to see the difference from the other two
    scopes.
    """

    def __init__(self) -> None:
        """Take the next correlation number."""
        self.number = next(_request_numbers)


class JobHandler:
    """Handles one job.

    `notifier` is declared `Notifier | None`, so this handler works whether or not a notifier
    is registered. The container injects `None` when it is not.
    """

    def __init__(
        self,
        repository: Repository,
        clock: Clock,
        context: RequestContext,
        notifier: Notifier | None,
    ) -> None:
        """Take everything needed to handle a job."""
        self.repository = repository
        self.clock = clock
        self.context = context
        self.notifier = notifier

    async def handle(self, job: Job) -> JobResult:
        """Handle `job`, notifying if a notifier is configured."""
        result = JobResult(
            job_id=job.id,
            handled_at=self.clock.now(),
            notified=self.notifier is not None,
        )
        await self.repository.save(result)
        if self.notifier is not None:
            self.notifier.notify(f"handled {job.id}")
        return result


class ArchiveJobHandler:
    """A handler whose dependency is deliberately never registered.

    Resolving it fails, which is how the demo shows what a resolution error reports.
    """

    def __init__(self, store: ArchiveStore) -> None:
        """Take the archive store that `wiring.py` deliberately does not bind."""
        self.store = store


class JobDispatcher:
    """Resolves a handler per job, at dispatch time.

    Takes the `Container` itself. That is self-injection: the container hands back whichever
    container is doing the resolving, so a dispatcher resolved inside a scope resolves its
    handlers from that same scope. It is also how a cycle is broken — a dispatcher that
    depended on every handler eagerly, while handlers depend on the dispatcher, could not be
    constructed at all.
    """

    def __init__(self, container: Container) -> None:
        """Hold the resolving container."""
        self.container = container

    async def dispatch(self, job: Job) -> JobResult:
        """Resolve a handler for `job` and run it."""
        handler = await self.container.resolve(JobHandler)
        return await handler.handle(job)
