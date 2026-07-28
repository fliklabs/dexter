"""Exceptions raised by the CQRS module.

Two conventions carry over from the rest of dexter. `args` stays a short one-line message, so
`pytest.raises(match=...)` and log lines remain readable, while `__str__` appends whatever
detail the failure carries. And a wiring mistake is reported while wiring: everything under
`CqrsRegistrationError` is raised before the container is ever built.
"""

from collections.abc import Sequence
from typing import Any, Self

from dexter.commons import DexterError, describe_type


class CqrsError(DexterError):
    """Base class for every CQRS failure.

    Consumers can catch this to cover both wiring and dispatch problems.
    """


# ── registration ─────────────────────────────────────────────────────


class CqrsRegistrationError(CqrsError):
    """A handler or middleware could not be registered. Raised while wiring."""


class CqrsNotWiredError(CqrsRegistrationError):
    """`use_cqrs` was never called on this builder.

    The registries a handler registers into are created by `use_cqrs`, so it has to run
    first. Raised instead of the container's own "not registered as an instance" message,
    which names an internal type rather than the call the reader is missing.
    """


class DuplicateHandlerError(CqrsRegistrationError):
    """The message already has this handler.

    A command or query takes exactly one handler, so a second is always a mistake. An event
    takes many, but registering the *same* handler twice would silently run it twice.
    """


class InvalidHandlerError(CqrsRegistrationError):
    """The handler cannot be used.

    Covers a handler that is not a class, is a `Protocol`, has no `handle` method, or whose
    `handle` is not asynchronous.
    """


class DuplicateMiddlewareError(CqrsRegistrationError):
    """The middleware is already in the pipeline, and would run twice."""


class UnparameterizedMessageError(CqrsRegistrationError):
    """A command or query was declared without saying what it produces.

    `class CreateUser(Command)` leaves the result type unknown, so neither the type checker
    nor the registration check can say what its handler must return. Write
    `class CreateUser(Command[UserId])`, or `Command[None]` when there is no result.
    """


class HandlerResultMismatchError(CqrsRegistrationError):
    """The handler does not return what its message declares.

    A type checker catches a handler wired to the wrong *message*, but it cannot express "the
    result of this handler must match the parameter of that command" — so this half is checked
    here, when the binding is recorded, rather than surfacing as a lie about the dispatch's
    return type at runtime.
    """

    def __init__(
        self, message_type: type[Any], declared: object, returned: object
    ) -> None:
        """Name the message, what it promises, and what its handler actually returns."""
        super().__init__(
            f"{describe_type(message_type)} declares a result of "
            f"{describe_type(declared)}, but its handler returns "
            f"{describe_type(returned)}."
        )
        self.message_type = message_type
        self.declared = declared
        self.returned = returned


# ── dispatch ─────────────────────────────────────────────────────────


class DispatchError(CqrsError):
    """A message could not be dispatched."""


class UnhandledMessageError(DispatchError):
    """No handler is registered for the message.

    Raised synchronously by `dispatch` and `ask`, before any work starts, so an unwired
    message fails at the call site rather than inside a ticket nobody redeems.
    """

    def __init__(self, message_type: type[Any], kind: str, register_with: str) -> None:
        """Name the unhandled message and the call that would register it."""
        super().__init__(
            f"no handler is registered for the {kind} "
            f"{describe_type(message_type)}. Register one with {register_with}."
        )
        self.message_type = message_type


class UnhandledCommandError(UnhandledMessageError):
    """No handler is registered for the command."""

    def __init__(self, message_type: type[Any]) -> None:
        """Name the unhandled command."""
        super().__init__(message_type, "command", "`register_command_handler`")


class UnhandledQueryError(UnhandledMessageError):
    """No handler is registered for the query."""

    def __init__(self, message_type: type[Any]) -> None:
        """Name the unhandled query."""
        super().__init__(message_type, "query", "`register_query_handler`")


class CqrsGroupError(CqrsError, ExceptionGroup[Exception]):
    """Base for failures that are genuinely plural.

    Independent work — several event handlers, several unredeemed dispatches — has no
    privileged first failure, so reporting one and discarding the rest loses information the
    caller needs. Handle the arms with `except*`, or catch the whole group as a `CqrsError`.
    """

    subject: type[Any] | None
    """The message type these failures are about, when they are all about one."""

    def __new__(
        cls,
        message: str,
        exceptions: tuple[Exception, ...],
        subject: type[Any] | None = None,
    ) -> Self:
        """Build the group; `BaseExceptionGroup` constructs through `__new__`, not `__init__`."""
        instance = super().__new__(cls, message, exceptions)
        instance.subject = subject
        return instance

    # The supertype declares `derive` as an overloaded generic returning
    # `ExceptionGroup[_ExceptionT]`, which no fixed `ExceptionGroup[Exception]` subclass can
    # satisfy: narrowing the element type is precisely what it cannot do.
    def derive(self, excs: Sequence[Exception], /) -> Self:  # type: ignore[override]
        """Keep the concrete class when `except*` splits the group.

        Without this, splitting produces a plain `ExceptionGroup` and the unhandled remainder
        stops being catchable as a `CqrsError`.
        """
        return type(self)(self.args[0], tuple(excs), self.subject)


class EventHandlingError(CqrsGroupError):
    """One or more of an event's handlers failed.

    Every handler runs, whatever the others do — one failing reaction must not silence the
    rest — so the failures arrive together once they all have.
    """

    @property
    def event_type(self) -> type[Any] | None:
        """The event whose handlers failed."""
        return self.subject


class DispatchFailedError(CqrsGroupError):
    """Dispatches failed and were never redeemed.

    Raised by `MessageBus.drain`. A failure that `await ticket.result()` already raised is not
    included: it has been reported to someone who asked for it.

    `subject` is `None`: a drain covers whatever the bus happened to be carrying, which is not
    one message type.
    """


# ── state ────────────────────────────────────────────────────────────


class CqrsStateError(CqrsError):
    """A bus was used after it was closed."""


class BusClosedError(CqrsStateError):
    """The bus has been closed and can no longer accept messages."""
