"""The container: async resolution, scopes, and the concurrency guarantee.

A container is created by `ContainerBuilder.build()` and its registry is immutable
thereafter. `scope()` produces a child that shares that registry and keeps its own instance
cache.

**Concurrency contract.** A container belongs to one event loop. It holds no locks: mutual
exclusion during cached construction comes from a per-key in-flight `asyncio.Task`. The first
resolver creates the task, every concurrent resolver awaits it through `asyncio.shield`, and
the instance is cached exactly once — so concurrent resolutions of the same key yield the same
object, and a resolver that is cancelled does not cancel construction for the others. If
construction fails, the failure reaches every waiter and the in-flight entry is discarded, so a
later resolve retries rather than replaying a cached failure. Sharing a container across event
loops or threads is unsupported.
"""

import asyncio
import inspect
from types import MappingProxyType, TracebackType
from typing import Any, Never, Self

from .errors import (
    CircularDependencyError,
    ContainerClosedError,
    InvalidRegistrationError,
    ResolutionDepthExceededError,
    ScopeClosedError,
    ScopeRequiredError,
    UnregisteredDependencyError,
)
from .models import (
    DependencyPlan,
    Key,
    ParameterKind,
    Registration,
    ResolutionChain,
    ResolutionStep,
    Scope,
    describe_key,
)


class Container:
    """Resolves dependencies from an immutable set of registrations.

    The root container holds `Scope.SINGLETON` instances; each child scope holds its own
    `Scope.SCOPED` instances. `Scope.TRANSIENT` is never cached.
    """

    __slots__ = (
        "_cache",
        "_closed",
        "_in_flight",
        "_parent",
        "_plans",
        "_registry",
        "_root",
    )

    # Declared explicitly because `_root` is assigned from `parent._root`, which mypy cannot
    # infer from the assignment alone. Bare annotations create no class attributes, so they
    # do not conflict with `__slots__`.
    _registry: MappingProxyType[Any, Registration]
    _plans: MappingProxyType[Any, DependencyPlan]
    _parent: Container | None
    _root: Container
    _cache: dict[Any, Any]
    _in_flight: dict[Any, asyncio.Task[Any]]
    _closed: bool

    def __init__(
        self,
        registry: MappingProxyType[Any, Registration],
        plans: MappingProxyType[Any, DependencyPlan],
        parent: Container | None = None,
    ) -> None:
        """Build a container over a frozen registry; `parent` makes this a child scope."""
        self._registry = registry
        self._plans = plans
        self._parent = parent
        self._root = self if parent is None else parent._root
        self._cache: dict[Any, Any] = {}
        self._in_flight: dict[Any, asyncio.Task[Any]] = {}
        self._closed = False

    # ── public API ───────────────────────────────────────────────────

    def is_registered(self, key: Key[Any], /) -> bool:
        """Whether `key` has a binding."""
        return key in self._registry

    async def resolve[T](self, key: Key[T], /) -> T:
        """Resolve `key`, constructing it and its dependencies as needed.

        Raises `UnregisteredDependencyError` if `key` has no binding — dexter never
        constructs an unregistered type.
        """
        self._ensure_open()
        result: T = await self._resolve(key, ResolutionChain(), None)
        return result

    async def try_resolve[T](self, key: Key[T], /) -> T | None:
        """Resolve `key`, or return `None` if it has no binding.

        Only an absent binding yields `None`. A binding that exists but fails to construct
        still raises, because that is a wiring bug rather than a missing optional feature.
        """
        self._ensure_open()
        # `Container` is resolvable without being registered, so it must be excluded here or
        # `try_resolve` would disagree with `resolve` about it.
        if key is not Container and key not in self._registry:
            return None
        result: T = await self._resolve(key, ResolutionChain(), None)
        return result

    def scope(self) -> Container:
        """Create a child scope sharing this container's registry.

        Use it as an async context manager so scoped lifetimes end deterministically::

            async with container.scope() as scope:
                handler = await scope.resolve(Handler)
        """
        self._ensure_open()
        return Container(self._registry, self._plans, parent=self)

    async def aclose(self) -> None:
        """Close this container or scope. Idempotent."""
        if self._closed:
            return
        self._closed = True
        await self._cancel_in_flight()
        self._cache.clear()

    async def __aenter__(self) -> Self:
        """Enter the scope, returning it."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the scope on exit."""
        await self.aclose()

    # ── resolution ───────────────────────────────────────────────────

    async def _resolve(
        self, key: Any, chain: ResolutionChain, parameter: str | None
    ) -> Any:
        if key is Container:
            # Self-injection hands back whichever container is doing the resolving, so a
            # dependency resolved inside a scope sees that scope rather than the root.
            return self

        registration = self._registry.get(key)
        if registration is None:
            raise UnregisteredDependencyError(
                key, chain.extend(ResolutionStep(key, parameter))
            )

        if registration.has_instance:
            return registration.instance

        # The cycle and depth checks happen here, before the in-flight map is consulted, and
        # that ordering is load-bearing. For a cached scope the second request for a key
        # already under construction would otherwise await its in-flight task — a task that
        # is itself blocked on the other half of the cycle — and deadlock instead of raising.
        if chain.contains_eager(key):
            raise CircularDependencyError(
                key, chain.extend(ResolutionStep(key, parameter))
            )
        if chain.depth >= ResolutionChain.MAX_DEPTH:
            raise ResolutionDepthExceededError(
                key, chain.extend(ResolutionStep(key, parameter))
            )

        if registration.scope is Scope.TRANSIENT:
            # Nothing to share, so no task and no cache entry: constructed inline.
            return await self._produce(registration, chain, parameter)

        if registration.scope is Scope.SCOPED and self._parent is None:
            # `Scoped` means one instance per scoped container, and the root is not one.
            # Caching it here instead would silently make it a process-wide singleton.
            raise ScopeRequiredError(key, chain.extend(ResolutionStep(key, parameter)))

        owner = self._root if registration.scope is Scope.SINGLETON else self
        return await owner._resolve_cached(registration, chain, parameter)

    async def _resolve_cached(
        self, registration: Registration, chain: ResolutionChain, parameter: str | None
    ) -> Any:
        key = registration.key
        if key in self._cache:
            # Hot path: a plain dict hit, with no task and no suspension.
            return self._cache[key]

        task = self._in_flight.get(key)
        if task is None:
            # There is no `await` between the lookup above and the insert below, so on a
            # single event loop this is atomic and the map itself is the mutual exclusion.
            task = asyncio.create_task(
                self._produce(registration, chain, parameter),
                name=f"dexter.resolve:{describe_key(key)}",
            )
            self._in_flight[key] = task
            task.add_done_callback(lambda done: self._settle(key, done))

        # `shield` stops a cancelled waiter from cancelling construction for everyone else.
        return await asyncio.shield(task)

    def _settle(self, key: Any, task: asyncio.Task[Any]) -> None:
        self._in_flight.pop(key, None)
        if task.cancelled():
            return
        if task.exception() is None:
            self._cache[key] = task.result()
        # Calling `exception()` marks a failure as retrieved, so an abandoned one does not
        # log "Task exception was never retrieved". Nothing is cached on failure, so a later
        # resolve retries instead of replaying it.

    async def _produce(
        self, registration: Registration, chain: ResolutionChain, parameter: str | None
    ) -> Any:
        # Cycle and depth are already checked in `_resolve`, before the in-flight map.
        key = registration.key
        own_chain = chain.extend(ResolutionStep(key, parameter))
        kwargs = await self._resolve_parameters(self._plans[key], own_chain)
        provider = registration.provider
        result = provider(**kwargs)

        if registration.is_async:
            return await result
        if inspect.isawaitable(result):
            # Classified synchronous but produced an awaitable. Awaiting it anyway would make
            # the async boundary unanalysable and make an awaitable-valued service
            # impossible to register. Close it so no "never awaited" warning escapes.
            closer = getattr(result, "close", None)
            if callable(closer):
                closer()
            raise InvalidRegistrationError(
                f"{describe_key(provider)} is registered as a synchronous provider but "
                f"returned an awaitable. Declare it with `async def`, or mark it with "
                f"`inspect.markcoroutinefunction`."
            )
        return result

    async def _resolve_parameters(
        self, plan: DependencyPlan, chain: ResolutionChain
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for parameter in plan.parameters:
            if parameter.kind is ParameterKind.CONTAINER:
                kwargs[parameter.name] = self
                continue

            key = parameter.key
            if parameter.kind is ParameterKind.OPTIONAL and key not in self._registry:
                kwargs[parameter.name] = None
                continue

            try:
                kwargs[parameter.name] = await self._resolve(key, chain, parameter.name)
            except UnregisteredDependencyError as error:
                # A default covers *this* parameter's key, not the whole subtree beneath it.
                # Swallowing a deeper failure would hide a real wiring mistake behind a
                # plausible-looking default value.
                if parameter.has_default and error.key is key:
                    continue
                raise
        return kwargs

    # ── internals ────────────────────────────────────────────────────

    def _ensure_open(self) -> None:
        # The whole ancestry is checked, not just this container. A scope holds its own cache
        # but resolves singletons through its root, so a live child of a closed parent could
        # otherwise keep working — and, worse, rebuild a singleton on the closed root and
        # cache it there, quietly reviving a container that was meant to be finished with.
        container: Container | None = self
        while container is not None:
            if container._closed:
                self._raise_closed(container)
            container = container._parent

    def _raise_closed(self, closed: Container) -> Never:
        ancestor = "" if closed is self else " an ancestor of this scope is closed: "
        if closed._parent is None:
            raise ContainerClosedError(
                f"{ancestor}the container is closed."
                if ancestor
                else "this container is closed."
            )
        raise ScopeClosedError(
            f"{ancestor}that scope has exited."
            if ancestor
            else "this scope has exited."
        )

    async def _cancel_in_flight(self) -> None:
        tasks = list(self._in_flight.values())
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._in_flight.clear()
