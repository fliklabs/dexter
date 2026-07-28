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

    async def drain(self) -> None:
        """Wait for everything this bus started, then report what failed unobserved.

        Raises `DispatchFailedError` — an `ExceptionGroup` — if any ticket failed and was
        never redeemed. Draining twice is safe: the second call has nothing left to wait for.
        """
        outstanding = tuple(self._outstanding)
        self._outstanding.clear()
        if not outstanding:
            return

        await asyncio.gather(
            *(ticket.task for ticket in outstanding), return_exceptions=True
        )

        failures = tuple(
            failure
            for ticket in outstanding
            if (failure := ticket.unobserved_failure()) is not None
        )
        if failures:
            raise DispatchFailedError(
                f"{len(failures)} of {len(outstanding)} {self.name} dispatches failed "
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
