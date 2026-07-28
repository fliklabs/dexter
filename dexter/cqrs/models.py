"""Value types for the CQRS module: the messages, the envelope, and the handler contracts.

The same split the rest of dexter uses applies here. A **message** is a pydantic model: it
crosses from the outside world into the application, is built once, and validation earns its
cost. An **envelope** is a slotted class: dexter builds one per dispatch and nobody outside
constructs one, so paying ~8x to validate it would be paying for nothing.

Messages carry no identity. Two equal commands are equal and hashable, and identity belongs to
the act of sending rather than to the value sent — so the same command dispatched twice yields
two distinguishable envelopes rather than one ambiguous id.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

type MessageId = str
"""Identifies one dispatch. A UUIDv7, so lexical order is chronological order."""


def new_message_id() -> MessageId:
    """Return a fresh, time-ordered message id."""
    return str(uuid.uuid7())


class Command[TResult](BaseModel):
    """An instruction to change state, producing `TResult`.

    The type parameter appears in no field. It exists so that dispatching is typed end to end:
    `dispatch` returns a `Dispatch[TResult]` and redeeming it yields a `TResult`, with no cast
    at the call site. Use `Command[None]` when there is nothing to return.

    Exactly one handler may be registered per command.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Query[TResult](BaseModel):
    """A request to read state, producing `TResult`.

    Queries are answered inline — `await query_bus.ask(query)` returns the result directly.
    There is no ticket, because a read has no side effect worth deferring and no identity worth
    correlating; deferring one would only be a slower read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Event(BaseModel):
    """A record that something has happened.

    Unparameterized, because an event may have any number of handlers and there is no single
    result to type. Publishing an event nobody handles is not an error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


type Message = Command[Any] | Query[Any] | Event
"""Any of the three message roles."""


class Envelope[TMessage]:
    """One dispatch of one message: the message plus everything true about *sending* it.

    Slotted rather than pydantic: dexter builds one per dispatch and no consumer constructs
    one, so there is no untrusted input to validate.

    `correlation_id` is shared by everything in one causal chain and defaults to this
    envelope's own id, so a message sent from nothing starts a new chain. `causation_id` names
    the single message that directly caused this one.
    """

    __slots__ = ("causation_id", "correlation_id", "id", "message", "sent_at")

    def __init__(
        self,
        message: TMessage,
        *,
        message_id: MessageId,
        sent_at: datetime,
        correlation_id: MessageId | None = None,
        causation_id: MessageId | None = None,
    ) -> None:
        """Record a message and the identity of this particular sending of it."""
        self.message = message
        self.id = message_id
        self.sent_at = sent_at
        self.correlation_id = correlation_id or message_id
        self.causation_id = causation_id

    @classmethod
    def wrap(
        cls, message: TMessage, *, caused_by: Envelope[Any] | None = None
    ) -> Envelope[TMessage]:
        """Wrap `message` for sending, generating its id and timestamp.

        Passing `caused_by` continues that envelope's chain: the new envelope inherits its
        `correlation_id` and records it as the cause.
        """
        message_id = new_message_id()
        return cls(
            message,
            message_id=message_id,
            sent_at=datetime.now(UTC),
            correlation_id=None if caused_by is None else caused_by.correlation_id,
            causation_id=None if caused_by is None else caused_by.id,
        )

    def __repr__(self) -> str:
        return f"Envelope(id={self.id!r}, message={type(self.message).__name__})"


# ── handler contracts ────────────────────────────────────────────────
#
# Protocols, so nothing has to inherit from dexter to be a handler. A handler is an ordinary
# class with one async method; the container constructs it, so its dependencies are ordinary
# annotated constructor parameters.


class CommandHandler[TCommand, TResult](Protocol):
    """Executes one command."""

    async def handle(self, command: TCommand) -> TResult:
        """Execute `command` and return its result."""
        ...


class QueryHandler[TQuery, TResult](Protocol):
    """Answers one query."""

    async def handle(self, query: TQuery) -> TResult:
        """Answer `query`."""
        ...


class EventHandler[TEvent](Protocol):
    """Reacts to one event.

    Returns nothing: an event is a statement of fact, and a reaction that needed to report
    something back would be a command.
    """

    async def handle(self, event: TEvent) -> None:
        """React to `event`."""
        ...


# ── middleware ───────────────────────────────────────────────────────

type Next = Callable[[Envelope[Any]], Awaitable[Any]]
"""Continues the pipeline. Takes an envelope so middleware can enrich it on the way down."""


class Middleware(Protocol):
    """Wraps every dispatch on every bus.

    One protocol rather than three, so a concern like logging or tracing is written once. The
    result is `Any` at this boundary and re-narrowed by the bus: middleware is generic over
    every message, and there is no useful type to give the result of an arbitrary one.
    """

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        """Do something around `call_next(envelope)`, and return its result.

        Not calling `call_next` short-circuits the dispatch, which is how a cache or an
        authorisation check refuses one.
        """
        ...
