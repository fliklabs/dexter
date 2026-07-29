"""Where every dependency is bound. This is the file to read first.

Binding is two calls — `register(Key).to(Provider, scope=...)` — because that is what lets a
type checker verify the provider actually produces the key. Try changing one of the providers
below to something that returns the wrong type and running `uv run mypy`: it is an error
before the code ever runs.

`scope` is required on every binding. There is no default, because lifetime is the most
consequential property of a binding and should not be chosen by omission.
"""

from dexter.dependency_injection import Container, ContainerBuilder, Scope

from .domain import Clock, Notifier, Repository, Settings
from .services import (
    ArchiveJobHandler,
    ConnectionPool,
    ConsoleNotifier,
    InMemoryRepository,
    JobDispatcher,
    JobHandler,
    RequestContext,
    SystemClock,
    UnitOfWork,
    open_pool,
)

DEFAULT_SETTINGS = Settings(dsn="postgres://demo/taskflow", worker_count=3)


def build_container(
    settings: Settings | None = None, *, with_notifier: bool = False
) -> Container:
    """Wire the application and return a container ready to resolve from.

    Args:
        settings: Configuration to bind. Defaults to `DEFAULT_SETTINGS`.
        with_notifier: Whether to bind a `Notifier`. Left unbound, every `Notifier | None`
            dependency receives `None`, which is how the demo shows optional dependencies
            without changing a single line of handler code.
    """
    builder = ContainerBuilder()

    # An instance built before the container. No scope is taken: an existing object is
    # inherently a single object.
    builder.register(Settings).to_instance(settings or DEFAULT_SETTINGS)

    # Singleton: one per container graph, shared by every scope. Opened by an async factory,
    # because `__init__` cannot await, and closed by `dispose=` when the container closes.
    # The callback is explicit: dexter never guesses that a method named `aclose` is the one
    # that releases a type.
    builder.register(ConnectionPool).to(
        open_pool, scope=Scope.SINGLETON, dispose=ConnectionPool.aclose
    )

    # Scoped with a `dispose=`, which is the pairing disposal exists for: one per scope, and
    # leaving the scope releases it. Released before the pool it depends on, because disposal
    # runs in reverse creation order.
    builder.register(UnitOfWork).to(
        UnitOfWork, scope=Scope.SCOPED, dispose=UnitOfWork.aclose
    )

    # A protocol key bound to a concrete class, with no suppression needed at the call site.
    builder.register(Clock).to(SystemClock, scope=Scope.SINGLETON)

    # Scoped: one per `container.scope()`. The natural lifetime for per-request state such as
    # a unit of work.
    builder.register(Repository).to(InMemoryRepository, scope=Scope.SCOPED)

    # Transient: a new instance on every single resolution.
    builder.register(RequestContext).to(RequestContext, scope=Scope.TRANSIENT)

    builder.register(JobHandler).to(JobHandler, scope=Scope.TRANSIENT)

    # Scoped, emphatically not Singleton. The dispatcher takes a `Container` and resolves
    # handlers from it, so its lifetime decides which container those handlers come from. As a
    # singleton it would capture the root and resolve every handler there, bypassing the scope
    # it was asked for — and since `JobHandler` needs the scoped `Repository`, that now fails
    # loudly with `ScopeRequiredError` instead of quietly sharing one repository everywhere.
    builder.register(JobDispatcher).to(JobDispatcher, scope=Scope.SCOPED)

    # Registered, but its own `ArchiveStore` dependency deliberately is not — so resolving it
    # demonstrates what a resolution failure reports.
    builder.register(ArchiveJobHandler).to(ArchiveJobHandler, scope=Scope.TRANSIENT)

    if with_notifier:
        builder.register(Notifier).to(ConsoleNotifier, scope=Scope.SINGLETON)

    return builder.build()
