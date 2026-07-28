"""The command bus: send an instruction, get a ticket.

`dispatch` is a plain `def`, not `async def`, and that is deliberate. A ticket you have to
await before you can read its id is not a ticket — the point of one is that the id exists the
moment the bus accepts the message, so it can be logged, correlated or returned to a caller
while the work is still running.

Accepting a message is not free of checks, though. The registry lookup is a dict hit, so the
common mistake — no handler registered for this command — raises *at the call site*,
synchronously, before any task exists. Only constructing the handler and running it are
deferred, because both are asynchronous.

`dispatch` requires a running event loop, since that is where the work goes. Nothing here
starts one.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from dexter.commons import describe_type
from dexter.dependency_injection import Container

from .bus import MessageBus
from .dispatch import Dispatch
from .models import Command, Envelope
from .pipeline import MiddlewarePipeline
from .registry import CommandRegistry


class CommandBus(MessageBus, ABC):
    """Sends commands to the single handler registered for each.

    An abstract key, bound to `InProcessCommandBus` by `use_cqrs`. Depending on this rather
    than on the implementation is what lets a different delivery mechanism be bound later
    without touching a call site.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        """What this bus carries."""
        return "command"

    @abstractmethod
    def dispatch[TResult](
        self,
        command: Command[TResult],
        /,
        *,
        caused_by: Envelope[Any] | None = None,
    ) -> Dispatch[TResult]:
        """Accept `command` and return a ticket for its result.

        Raises `UnhandledCommandError` immediately if nothing handles it. Pass `caused_by` to
        continue another message's causal chain.
        """


class InProcessCommandBus(CommandBus):
    """Runs each command's handler on this event loop.

    Takes the `Container` itself and resolves handlers from it at dispatch time, so a handler
    is constructed against whichever container this bus belongs to. That is why the bus must
    be registered `Scope.SCOPED`: a singleton would hold the root container and resolve every
    handler there, bypassing the scope it was asked for.
    """

    __slots__ = ("_container", "_pipeline", "_registry")

    def __init__(
        self,
        container: Container,
        registry: CommandRegistry,
        pipeline: MiddlewarePipeline,
    ) -> None:
        """Take the resolving container, the command registry, and the shared pipeline."""
        super().__init__()
        self._container = container
        self._registry = registry
        self._pipeline = pipeline

    def dispatch[TResult](
        self,
        command: Command[TResult],
        /,
        *,
        caused_by: Envelope[Any] | None = None,
    ) -> Dispatch[TResult]:
        """Accept `command`, start its handler, and return a ticket for the result."""
        self._ensure_open()
        handler_key = self._registry.resolve(type(command))
        envelope = Envelope.wrap(command, caused_by=caused_by)
        task: asyncio.Task[TResult] = asyncio.create_task(
            self._execute(envelope, handler_key),
            name=f"dexter.cqrs.command:{describe_type(type(command))}",
        )
        return self._track(Dispatch(envelope, task))

    async def _execute(self, envelope: Envelope[Any], handler_key: type[Any]) -> Any:
        async def run(target: Envelope[Any]) -> Any:
            handler = await self._container.resolve(handler_key)
            return await handler.handle(target.message)

        return await self._pipeline.run(self._container, envelope, run)
