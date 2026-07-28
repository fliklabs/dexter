"""The ticket a bus hands back when it accepts a message.

Sending is always two moments: the bus accepts the message *now*, and the result is ready
*later*. A ticket names both. It carries the envelope's id immediately — so the send can be
logged, correlated or handed to a caller before any work finishes — and `result()` redeems the
outcome whenever the holder is ready for it.

The concurrency behaviour mirrors `Container`'s in-flight task map, for the same reasons and
with the same guarantees: the work runs in one `asyncio.Task`, every holder awaits it through
`asyncio.shield`, and a holder who is cancelled does not cancel the work for anyone else.
"""

import asyncio
from typing import Any

from .models import Envelope, MessageId


class Dispatch[TResult]:
    """A handle on one dispatched message.

    Redeem it with `await ticket.result()`, as many times as you like — the work runs once and
    every redemption yields the same value. Never redeeming it is also fine: the work still
    runs, and the bus that issued the ticket will surface any failure from `drain()`.
    """

    __slots__ = ("_observed", "_task", "envelope")

    def __init__(self, envelope: Envelope[Any], task: asyncio.Task[TResult]) -> None:
        """Hold the envelope that was sent and the task producing its result."""
        self.envelope = envelope
        self._task = task
        self._observed = False
        # Retrieving the exception here stops Python logging "Task exception was never
        # retrieved" for a ticket nobody redeems. The task keeps the exception, so a later
        # `result()` still raises it, and the issuing bus still reports it from `drain()`.
        task.add_done_callback(_mark_retrieved)

    @property
    def id(self) -> MessageId:
        """The id of this dispatch, available before the work finishes."""
        return self.envelope.id

    @property
    def correlation_id(self) -> MessageId:
        """The id shared by everything in this dispatch's causal chain."""
        return self.envelope.correlation_id

    @property
    def task(self) -> asyncio.Task[TResult]:
        """The task doing the work. The issuing bus waits on this when draining."""
        return self._task

    def done(self) -> bool:
        """Whether the result is ready, without awaiting it."""
        return self._task.done()

    async def result(self) -> TResult:
        """Wait for the result, raising whatever the handler raised.

        Shielded: cancelling the coroutine that awaits this does not cancel the underlying
        work, because other holders of the same ticket — and the bus's own `drain()` — are
        entitled to the result regardless.

        Redeeming a ticket also marks its outcome as *observed*, which is what stops `drain()`
        raising a failure the caller has already handled.
        """
        self._observed = True
        return await asyncio.shield(self._task)

    def unobserved_failure(self) -> Exception | None:
        """The exception this dispatch failed with, if it failed and nobody redeemed it.

        `None` when the work succeeded, is still running, was cancelled, or when `result()`
        already raised the failure to a caller who asked for it.
        """
        if self._observed or not self._task.done() or self._task.cancelled():
            return None
        failure = self._task.exception()
        return failure if isinstance(failure, Exception) else None

    def __repr__(self) -> str:
        state = "done" if self._task.done() else "pending"
        return f"Dispatch(id={self.id!r}, {state})"


class EventDispatch(Dispatch[None]):
    """A handle on one published event.

    `handler_count` is how many handlers the event had when it was published. Zero is a normal
    outcome rather than an error, and reporting it is what keeps publishing into silence
    observable instead of invisible.
    """

    __slots__ = ("handler_count",)

    def __init__(
        self, envelope: Envelope[Any], task: asyncio.Task[None], handler_count: int
    ) -> None:
        """Hold the envelope, the fan-out task, and how many handlers it covers."""
        super().__init__(envelope, task)
        self.handler_count = handler_count

    def __repr__(self) -> str:
        state = "done" if self._task.done() else "pending"
        return f"EventDispatch(id={self.id!r}, {state}, handlers={self.handler_count})"


def _mark_retrieved(task: asyncio.Task[Any]) -> None:
    """Retrieve a finished task's exception so an unredeemed failure logs nothing."""
    if not task.cancelled():
        task.exception()
