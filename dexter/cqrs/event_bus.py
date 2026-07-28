"""The event bus: announce something happened, to everyone who cares.

Three things distinguish an event from a command, and all three follow from "many handlers,
none privileged":

- **Every handler runs**, concurrently. One failing reaction must not stop the others, so the
  fan-out never short-circuits.
- **Failures arrive together**, as an `EventHandlingError` — an `ExceptionGroup` — once every
  handler has finished. Reporting only the first would hide the rest.
- **No handlers is not an error.** An event nobody listens to is a normal state, not a broken
  wiring; the ticket reports `handler_count == 0` so the silence is still visible.

Middleware wraps the publish, not each handler: one dispatch is one pass through the pipeline,
the same as a command. A concern that belongs per handler belongs in the handler.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from dexter.commons import describe_type
from dexter.dependency_injection import Container

from .bus import MessageBus
from .dispatch import EventDispatch
from .errors import EventHandlingError
from .models import Envelope, Event
from .pipeline import MiddlewarePipeline
from .registry import EventRegistry


class EventBus(MessageBus, ABC):
    """Publishes events to every handler registered for them."""

    __slots__ = ()

    @property
    def name(self) -> str:
        """What this bus carries."""
        return "event"

    @abstractmethod
    def publish(
        self, event: Event, /, *, caused_by: Envelope[Any] | None = None
    ) -> EventDispatch:
        """Accept `event` and return a ticket covering every handler's completion."""


class InProcessEventBus(EventBus):
    """Runs every handler for an event concurrently on this event loop.

    Not awaiting the returned ticket is the deferred case: the handlers still run, and any
    failure surfaces from `drain()` instead.
    """

    __slots__ = ("_container", "_pipeline", "_registry")

    def __init__(
        self,
        container: Container,
        registry: EventRegistry,
        pipeline: MiddlewarePipeline,
    ) -> None:
        """Take the resolving container, the event registry, and the shared pipeline."""
        super().__init__()
        self._container = container
        self._registry = registry
        self._pipeline = pipeline

    def publish(
        self, event: Event, /, *, caused_by: Envelope[Any] | None = None
    ) -> EventDispatch:
        """Accept `event`, start every handler, and return a ticket for their completion."""
        self._ensure_open()
        handler_keys = self._registry.resolve(type(event))
        envelope = Envelope.wrap(event, caused_by=caused_by)
        task: asyncio.Task[None] = asyncio.create_task(
            self._publish(envelope, handler_keys),
            name=f"dexter.cqrs.event:{describe_type(type(event))}",
        )
        return self._track(EventDispatch(envelope, task, len(handler_keys)))

    async def _publish(
        self, envelope: Envelope[Any], handler_keys: tuple[type[Any], ...]
    ) -> None:
        async def fan_out(target: Envelope[Any]) -> None:
            await self._run_handlers(target, handler_keys)

        await self._pipeline.run(self._container, envelope, fan_out)

    async def _run_handlers(
        self, envelope: Envelope[Any], handler_keys: tuple[type[Any], ...]
    ) -> None:
        if not handler_keys:
            return

        outcomes = await asyncio.gather(
            *(self._run_handler(envelope, key) for key in handler_keys),
            return_exceptions=True,
        )

        # `return_exceptions=True` captures `BaseException` too. A cancellation is this task
        # being torn down, not an event handler misbehaving, so it propagates on its own
        # rather than being folded into a group of ordinary failures.
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, Exception
            ):
                raise outcome

        failures = tuple(
            outcome for outcome in outcomes if isinstance(outcome, Exception)
        )
        if failures:
            event_type = type(envelope.message)
            raise EventHandlingError(
                f"{len(failures)} of {len(handler_keys)} handlers for "
                f"{describe_type(event_type)} failed.",
                failures,
                event_type,
            )

    async def _run_handler(self, envelope: Envelope[Any], key: type[Any]) -> None:
        handler = await self._container.resolve(key)
        await handler.handle(envelope.message)
