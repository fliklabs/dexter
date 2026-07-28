"""Wiring: how a CQRS application is registered into a container.

Two shapes, and the difference between them is the whole convention:

- **`use_cqrs(builder)`** registers what the *module* provides — the registries, the pipeline,
  and the three buses. It takes no configuration, because it is a topology switch rather than
  a settings object: a different topology would be a different `use_*` function, not a flag.
- **`register_*(builder, ...)`** registers what the *application* contributes. Called once per
  handler, and `scope=` is required on each, for the same reason `Binder.to` requires it.

`use_cqrs` must run first: the `register_*` functions populate registries that it creates.
Calling them in the other order raises `CqrsNotWiredError` naming the call that is missing.

Neither shape returns the builder. `ContainerBuilder` is not a chaining API — `register`
returns a `Binder` and `build` returns a `Container` — so returning it here would invent a
second style for no gain.

    builder = ContainerBuilder()
    use_cqrs(builder)
    register_command_handler(builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT)
    container = builder.build()

    async with container.scope() as scope:
        bus = await scope.resolve(CommandBus)
        user_id = await bus.dispatch(CreateUser(email="a@b.c")).result()
"""

from typing import Any

from dexter.commons import describe_type
from dexter.dependency_injection import (
    ContainerBuilder,
    InvalidRegistrationError,
    Scope,
)

from .command_bus import CommandBus, InProcessCommandBus
from .errors import CqrsNotWiredError
from .event_bus import EventBus, InProcessEventBus
from .models import (
    Command,
    CommandHandler,
    Event,
    EventHandler,
    Middleware,
    Query,
    QueryHandler,
)
from .pipeline import MiddlewarePipeline
from .query_bus import InProcessQueryBus, QueryBus
from .registry import CommandRegistry, EventRegistry, QueryRegistry


def use_cqrs(builder: ContainerBuilder) -> None:
    """Register the registries, the middleware pipeline, and the three buses.

    Call once, before registering any handler.

    The registries and the pipeline are bound as instances so that the `register_*` functions
    can fetch and populate them while wiring, before the container is built.

    Every bus is `Scope.SCOPED`, and that is not a preference. A bus resolves handlers from
    the container it was given, so its lifetime decides which container those handlers come
    from; as a singleton it would capture the root and resolve every handler there, bypassing
    the scope it was asked for. Resolve a bus from inside `container.scope()`.
    """
    builder.register(CommandRegistry).to_instance(CommandRegistry())
    builder.register(QueryRegistry).to_instance(QueryRegistry())
    builder.register(EventRegistry).to_instance(EventRegistry())
    builder.register(MiddlewarePipeline).to_instance(MiddlewarePipeline())

    builder.register(CommandBus).to(InProcessCommandBus, scope=Scope.SCOPED)
    builder.register(QueryBus).to(InProcessQueryBus, scope=Scope.SCOPED)
    builder.register(EventBus).to(InProcessEventBus, scope=Scope.SCOPED)


def register_command_handler[TCommand: Command[Any], TResult](
    builder: ContainerBuilder,
    command: type[TCommand],
    handler: type[CommandHandler[TCommand, TResult]],
    *,
    scope: Scope,
) -> None:
    """Bind `command` to `handler`, in the container and in the registry.

    A handler taking the wrong command is a type error. A handler returning something other
    than what the command declares cannot be expressed as one, so it is checked here and
    raises `HandlerResultMismatchError`.
    """
    _fetch(builder, CommandRegistry).register(command, handler)
    _register(builder, handler, scope)


def register_query_handler[TQuery: Query[Any], TResult](
    builder: ContainerBuilder,
    query: type[TQuery],
    handler: type[QueryHandler[TQuery, TResult]],
    *,
    scope: Scope,
) -> None:
    """Bind `query` to `handler`, in the container and in the registry."""
    _fetch(builder, QueryRegistry).register(query, handler)
    _register(builder, handler, scope)


def register_event_handler[TEvent: Event](
    builder: ContainerBuilder,
    event: type[TEvent],
    handler: type[EventHandler[TEvent]],
    *,
    scope: Scope,
) -> None:
    """Add `handler` to the handlers for `event`, in the container and in the registry.

    An event may have any number of handlers; they run concurrently and in no guaranteed
    order relative to one another.
    """
    _fetch(builder, EventRegistry).register(event, handler)
    _register(builder, handler, scope)


def register_middleware(
    builder: ContainerBuilder,
    middleware: type[Middleware],
    *,
    scope: Scope,
) -> None:
    """Append `middleware` to the pipeline every bus runs dispatches through.

    Order is registration order, outermost first: the first middleware registered sees a
    dispatch before every other one, and sees its result after every other one.
    """
    _fetch(builder, MiddlewarePipeline).add(middleware)
    _register(builder, middleware, scope)


def _register(builder: ContainerBuilder, target: type[Any], scope: Scope) -> None:
    """Bind a handler or middleware as its own key, so the bus can resolve it by class.

    Always called *after* the registry has accepted the registration, so that a malformed or
    duplicate handler is reported by the precise CQRS error rather than by whichever of the
    container's own guards happened to trip first.

    Deliberately not tolerant of a repeat: binding one class for two different messages would
    make its `scope=` ambiguous, and `DuplicateRegistrationError` says so. Write a handler per
    message instead.
    """
    builder.register(target).to(target, scope=scope)


def _fetch[T](builder: ContainerBuilder, key: type[T]) -> T:
    """Fetch a registry or the pipeline from the builder, or explain that wiring is missing."""
    try:
        return builder.resolve_instance(key)
    except InvalidRegistrationError as error:
        raise CqrsNotWiredError(
            f"{describe_type(key)} is not registered, so there is nothing to register into. "
            f"Call `use_cqrs(builder)` before registering handlers or middleware."
        ) from error
