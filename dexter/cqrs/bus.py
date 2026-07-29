"""What every bus does regardless of what it carries.

A bus accepts messages until it is closed, and it owns whatever work it started. That
ownership is the whole reason this class exists: a bus hands out tickets, and a ticket nobody
redeems still represents real work that has to finish somewhere and whose failure has to
surface somewhere.

`drain()` is that somewhere. It waits for everything outstanding and raises whatever failed
*and was never looked at* — a failure already raised by `await ticket.result()` has been
reported to someone who asked for it, and reporting it twice would make ordinary error
handling produce a second exception at scope exit.

**A bus is closed by whoever owns the scope, not by the container.** `Container.aclose()` does
not yet call anything on the instances it resolved, so a scope cannot drain its buses for you.
Until it can, drain before leaving the scope — the reference application shows the shape.
"""

import asyncio
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Self

from .dispatch import Dispatch
from .errors import BusClosedError, DispatchFailedError


class BusGroup:
    """Every bus in one scope, settled as one.

    This exists because draining is not like other teardown. Releasing a resource only ever
    *removes* work, so releasing in reverse creation order is enough — but draining a bus
    **creates** work on the other buses. A command handler that publishes an event is the
    central CQRS pattern, and the event bus is constructed *inside* that handler, so it
    finishes construction after the command bus and reverse order would drain it first, while
    it is still empty. Draining the command bus next then publishes into a bus that has
    already been settled, and the reaction escapes the scope entirely.

    So the buses settle together, in rounds, until every one of them is quiet. Only then are
    they closed.
    """

    __slots__ = ("_buses",)

    def __init__(self) -> None:
        """Start with no buses; each one registers itself when it is constructed."""
        self._buses: list[MessageBus] = []

    def include(self, bus: MessageBus, /) -> None:
        """Take responsibility for settling `bus`."""
        self._buses.append(bus)

    @property
    def pending(self) -> int:
        """How many dispatches across every bus in this scope have not finished."""
        return sum(bus.pending for bus in self._buses)

    async def settle(self) -> None:
        """Drain every bus until they are all quiet, then close them.

        Registered as the group's `dispose=`, so leaving a scope does this. Rounds continue
        while anything is still pending, because each round can produce work for a bus that
        has already been drained this round.

        Raises `DispatchFailedError` gathering every unredeemed failure from every bus. The
        buses are closed either way.
        """
        failures: list[Exception] = []
        try:
            while True:
                for bus in self._buses:
                    try:
                        await bus.drain()
                    except DispatchFailedError as error:
                        failures.extend(error.exceptions)
                if not self.pending:
                    break
        finally:
            for bus in self._buses:
                await bus.aclose()

        if failures:
            raise DispatchFailedError(
                f"{len(failures)} dispatch"
                f"{'' if len(failures) == 1 else 'es'} failed and were never redeemed.",
                tuple(failures),
            )


class MessageBus(ABC):
    """Common lifecycle for every bus: outstanding work, draining, and closing."""

    __slots__ = ("_closed", "_outstanding")

    def __init__(self) -> None:
        """Start open, owning nothing."""
        self._outstanding: set[Dispatch[Any]] = set()
        self._closed = False

    @property
    @abstractmethod
    def name(self) -> str:
        """What this bus carries, for error messages: `command`, `query` or `event`."""

    @property
    def pending(self) -> int:
        """How many of this bus's dispatches have not finished."""
        return sum(1 for ticket in self._outstanding if not ticket.task.done())

    async def drain(self) -> None:
        """Wait for everything this bus started, then report what failed unobserved.

        Loops rather than waiting once, because a handler may dispatch again: waiting on a
        single snapshot would return while the work that work started was still running.

        Raises `DispatchFailedError` — an `ExceptionGroup` — if any ticket failed and was
        never redeemed. Draining twice is safe: the second call has nothing left to wait for.
        """
        while pending := tuple(
            ticket for ticket in self._outstanding if not ticket.task.done()
        ):
            await asyncio.gather(
                *(ticket.task for ticket in pending), return_exceptions=True
            )

        settled = tuple(self._outstanding)
        self._outstanding.clear()
        failures = tuple(
            failure
            for ticket in settled
            if (failure := ticket.unobserved_failure()) is not None
        )
        if failures:
            raise DispatchFailedError(
                f"{len(failures)} of {len(settled)} {self.name} dispatches failed "
                f"and were never redeemed.",
                failures,
            )

    async def aclose(self) -> None:
        """Close the bus, cancelling anything still running. Idempotent.

        Cancels rather than waits, because closing is what you do when you are no longer
        interested in the outcome. Call `drain()` first when you are.
        """
        if self._closed:
            return
        self._closed = True
        outstanding = tuple(self._outstanding)
        self._outstanding.clear()
        for ticket in outstanding:
            ticket.task.cancel()
        if outstanding:
            await asyncio.gather(
                *(ticket.task for ticket in outstanding), return_exceptions=True
            )

    async def __aenter__(self) -> Self:
        """Enter the bus, returning it."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the bus on exit."""
        await self.aclose()

    # ── internals ────────────────────────────────────────────────────

    def _ensure_open(self) -> None:
        if self._closed:
            raise BusClosedError(
                f"this {self.name} bus is closed and can no longer accept messages."
            )

    def _track[TDispatch: Dispatch[Any]](self, ticket: TDispatch) -> TDispatch:
        """Take ownership of a ticket until the bus is drained or closed.

        Deliberately not released when the task finishes: a failure that finished unobserved
        is exactly what `drain()` exists to report, and discarding the ticket on completion
        would throw that away. A bus is scoped, so what it holds is bounded by one scope.
        """
        self._outstanding.add(ticket)
        return ticket
