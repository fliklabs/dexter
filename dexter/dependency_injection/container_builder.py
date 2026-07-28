"""Registration: `ContainerBuilder` and the fluent `Binder`.

Binding is two steps — `builder.register(Key).to(Implementation, scope=...)` — because that
is the only shape a type checker can verify. Collapsing it into one call means widening the
key parameter so an abstract class or `Protocol` can be used, and that widening makes mypy
infer the type variable as `object`, silently accepting a provider that produces the wrong
type. With the key pinned by `register` and the provider checked against the resulting
`Binder[T]`, both are verified.
"""

from types import MappingProxyType
from typing import Any

from dexter.commons import describe_type

from ._annotations import build_plan, is_async_provider, is_protocol
from .container import Container
from .errors import (
    CaptiveDependencyError,
    DuplicateRegistrationError,
    IncompleteRegistrationError,
    InvalidRegistrationError,
)
from .models import (
    DependencyPlan,
    Key,
    ParameterKind,
    Provider,
    Registration,
    Scope,
    describe_scope,
)


class Binder[T]:
    """Completes a binding started by `ContainerBuilder.register`.

    Returned by `register` and not constructed directly. A `Binder` that is never completed
    leaves the key unbound, which `ContainerBuilder.build` rejects.
    """

    __slots__ = ("_builder", "_key")

    def __init__(self, builder: ContainerBuilder, key: Any) -> None:
        """Hold the builder to record into and the key being bound."""
        self._builder = builder
        self._key = key

    def to(self, provider: Provider[T], /, *, scope: Scope) -> None:
        """Bind the key to a class, a factory, or an async factory.

        `scope` is required: lifetime is the most consequential property of a binding and
        should never be chosen by omission.
        """
        if not callable(provider):
            raise InvalidRegistrationError(
                f"cannot bind {describe_type(self._key)} to {provider!r}, "
                f"which is not callable."
            )
        if is_protocol(provider):
            raise InvalidRegistrationError(
                f"cannot bind {describe_type(self._key)} to {describe_type(provider)}, "
                f"which is a Protocol; bind a concrete implementation or a factory."
            )
        self._builder._add(
            Registration(
                key=self._key,
                provider=provider,
                scope=scope,
                is_async=is_async_provider(provider),
            ),
            plan=build_plan(provider, Container),
        )

    def to_instance(self, instance: T, /) -> None:
        """Bind the key to an already-constructed value.

        No scope is taken: an existing instance is inherently a single instance.
        """
        self._builder._add(
            Registration(
                key=self._key,
                provider=type(instance),
                scope=Scope.SINGLETON,
                instance=instance,
                has_instance=True,
            ),
            plan=DependencyPlan(parameters=()),
        )


class ContainerBuilder:
    """Collects registrations and produces a `Container`.

    Registration happens here and resolution happens on the built container, so a container's
    registry is immutable and needs no locking.
    """

    __slots__ = ("_pending", "_plans", "_registry")

    def __init__(self) -> None:
        """Start with no registrations."""
        self._registry: dict[Any, Registration] = {}
        self._plans: dict[Any, DependencyPlan] = {}
        self._pending: dict[Any, None] = {}

    def register[T](self, key: Key[T], /) -> Binder[T]:
        """Begin binding `key`, returning a `Binder` to complete it.

        The key must be a class — including an abstract class or a `Protocol`. Complete the
        binding with `.to(...)` or `.to_instance(...)`.
        """
        if not isinstance(key, type):
            raise InvalidRegistrationError(
                f"{key!r} cannot be used as a key; a key must be a class."
            )
        if key in self._registry:
            raise DuplicateRegistrationError(
                f"{describe_type(key)} is already registered."
            )
        self._pending[key] = None
        return Binder(self, key)

    def is_registered(self, key: Key[Any], /) -> bool:
        """Whether `key` already has a completed binding."""
        return key in self._registry

    def resolve_instance[T](self, key: Key[T], /) -> T:
        """Return the instance bound to `key` by `to_instance`.

        This exists for the registry-mutation pattern: register a registry object as an
        instance, then let later wiring fetch and populate it before the container is built.
        """
        registration = self._registry.get(key)
        if registration is None or not registration.has_instance:
            raise InvalidRegistrationError(
                f"{describe_type(key)} is not registered as an instance."
            )
        instance: T = registration.instance
        return instance

    def build(self) -> Container:
        """Freeze the registrations into a `Container`.

        Raises `IncompleteRegistrationError` if any `register(...)` was never completed, and
        `CaptiveDependencyError` if a singleton would capture a scoped instance.
        """
        if self._pending:
            names = ", ".join(sorted(describe_type(key) for key in self._pending))
            raise IncompleteRegistrationError(
                f"registration was started but never completed for: {names}. "
                f"Call .to(...) or .to_instance(...)."
            )
        self._validate_lifetimes()
        return Container(
            MappingProxyType(dict(self._registry)),
            MappingProxyType(dict(self._plans)),
        )

    def _validate_lifetimes(self) -> None:
        """Reject any singleton that reaches a scoped key through its dependencies.

        A dependency has to live at least as long as whatever depends on it. Only one
        combination breaks that rule: a singleton outlives every scope, so it can never
        legitimately hold a scoped instance. `Scoped -> Singleton`, `Transient -> anything` and
        `Singleton -> Transient` are all fine.

        The walk is transitive, because a transient sitting in between changes nothing: the
        whole subgraph of a singleton is constructed once, on the root, so a scoped instance
        reached through it is captured just as permanently.
        """
        for key, registration in self._registry.items():
            if registration.scope is not Scope.SINGLETON or registration.has_instance:
                continue
            path = self._find_scoped_dependency(key, (describe_type(key),), {key})
            if path is not None:
                raise CaptiveDependencyError(
                    f"{describe_type(key)} is registered as {describe_scope(Scope.SINGLETON)} "
                    f"but depends on {path[-1]}, which is {describe_scope(Scope.SCOPED)}. A "
                    f"singleton outlives every scope, so it would capture one scope's instance "
                    f"and share it with all the others. Register the dependent as "
                    f"{describe_scope(Scope.SCOPED)}, or take a `Container` parameter and "
                    f"resolve the scoped dependency when you need it.",
                    path,
                )

    def _find_scoped_dependency(
        self, key: Any, path: tuple[str, ...], seen: set[Any]
    ) -> tuple[str, ...] | None:
        """Return the path to a scoped key reachable from `key`, or `None` if there is none."""
        plan = self._plans.get(key)
        if plan is None:
            return None
        for parameter in plan.parameters:
            # A `Container` parameter is a lazy boundary: resolution happens later, against
            # whichever container is asking, so nothing is captured here.
            if parameter.kind is ParameterKind.CONTAINER or parameter.key is None:
                continue
            dependency = self._registry.get(parameter.key)
            if dependency is None:
                continue  # Unregistered; resolution will report it far more clearly.
            step = f"{describe_type(parameter.key)} (parameter {parameter.name!r})"
            if dependency.scope is Scope.SCOPED:
                return (*path, step)
            if parameter.key in seen:
                continue  # A cycle; `CircularDependencyError` is the right report for it.
            found = self._find_scoped_dependency(
                parameter.key, (*path, step), seen | {parameter.key}
            )
            if found is not None:
                return found
        return None

    def _add(self, registration: Registration, plan: DependencyPlan) -> None:
        self._registry[registration.key] = registration
        self._plans[registration.key] = plan
        self._pending.pop(registration.key, None)
