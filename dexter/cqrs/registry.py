"""Which handler handles which message.

A registry is a plain map from a message class to the handler class that handles it. It holds
*classes*, never instances: the container constructs the handler at dispatch time, so a
handler's own dependencies follow the lifetime they were registered with rather than the
lifetime of the registry.

**Lookup is on the message's exact runtime class.** No walking up base classes, no matching by
name, no scanning modules at import time. A registration you cannot find by reading the wiring
is a registration nobody can reason about, and inheritance-based lookup makes "which handler
ran?" depend on MRO order.

A registry is mutable while wiring and read-only in practice thereafter: `use_cqrs` binds it as
an instance, the `register_*` functions populate it, and the container is built afterwards.
"""

from typing import Any

from dexter.commons import describe_type

from ._introspection import validate_handler
from .errors import (
    DuplicateHandlerError,
    UnhandledCommandError,
    UnhandledQueryError,
)
from .models import Command, Event, Query


class CommandRegistry:
    """Maps each command class to the single handler class that executes it."""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        """Start with nothing registered."""
        self._handlers: dict[type[Any], type[Any]] = {}

    def register(self, command: type[Any], handler: type[Any], /) -> None:
        """Record that `handler` executes `command`.

        Raises `DuplicateHandlerError` if the command already has one. Rebinding is not
        silently permitted: the winner would depend on the order unrelated wiring ran in.
        """
        validate_handler(command, handler, Command)
        existing = self._handlers.get(command)
        if existing is not None:
            raise DuplicateHandlerError(
                f"{describe_type(command)} is already handled by "
                f"{describe_type(existing)}; a command takes exactly one handler."
            )
        self._handlers[command] = handler

    def resolve(self, command_type: type[Any], /) -> type[Any]:
        """Return the handler class for `command_type`, or raise `UnhandledCommandError`."""
        handler = self._handlers.get(command_type)
        if handler is None:
            raise UnhandledCommandError(command_type)
        return handler

    def is_registered(self, command_type: type[Any], /) -> bool:
        """Whether `command_type` has a handler."""
        return command_type in self._handlers

    def registrations(self) -> tuple[tuple[type[Any], type[Any]], ...]:
        """Every (command, handler) pair, in registration order."""
        return tuple(self._handlers.items())


class QueryRegistry:
    """Maps each query class to the single handler class that answers it."""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        """Start with nothing registered."""
        self._handlers: dict[type[Any], type[Any]] = {}

    def register(self, query: type[Any], handler: type[Any], /) -> None:
        """Record that `handler` answers `query`."""
        validate_handler(query, handler, Query)
        existing = self._handlers.get(query)
        if existing is not None:
            raise DuplicateHandlerError(
                f"{describe_type(query)} is already handled by "
                f"{describe_type(existing)}; a query takes exactly one handler."
            )
        self._handlers[query] = handler

    def resolve(self, query_type: type[Any], /) -> type[Any]:
        """Return the handler class for `query_type`, or raise `UnhandledQueryError`."""
        handler = self._handlers.get(query_type)
        if handler is None:
            raise UnhandledQueryError(query_type)
        return handler

    def is_registered(self, query_type: type[Any], /) -> bool:
        """Whether `query_type` has a handler."""
        return query_type in self._handlers

    def registrations(self) -> tuple[tuple[type[Any], type[Any]], ...]:
        """Every (query, handler) pair, in registration order."""
        return tuple(self._handlers.items())


class EventRegistry:
    """Maps each event class to every handler class that reacts to it.

    Unlike a command, an event may have any number of handlers — including none, which is not
    an error. Registering the *same* handler twice is, because it would silently run twice.
    """

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        """Start with nothing registered."""
        self._handlers: dict[type[Any], list[type[Any]]] = {}

    def register(self, event: type[Any], handler: type[Any], /) -> None:
        """Add `handler` to the handlers for `event`, preserving registration order."""
        validate_handler(event, handler, Event)
        handlers = self._handlers.setdefault(event, [])
        if handler in handlers:
            raise DuplicateHandlerError(
                f"{describe_type(handler)} is already registered for "
                f"{describe_type(event)}; it would run twice."
            )
        handlers.append(handler)

    def resolve(self, event_type: type[Any], /) -> tuple[type[Any], ...]:
        """Return every handler class for `event_type`, in registration order.

        An event with no handlers yields an empty tuple. Publishing into silence is a normal
        state for an event, not a wiring error.
        """
        return tuple(self._handlers.get(event_type, ()))

    def is_registered(self, event_type: type[Any], /) -> bool:
        """Whether `event_type` has at least one handler."""
        return bool(self._handlers.get(event_type))

    def registrations(self) -> tuple[tuple[type[Any], tuple[type[Any], ...]], ...]:
        """Every (event, handlers) pair, in registration order."""
        return tuple(
            (event, tuple(handlers)) for event, handlers in self._handlers.items()
        )
