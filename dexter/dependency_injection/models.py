"""Value types for the dependency injection module.

Two kinds of type live here, and the distinction is deliberate:

- **Pydantic models** for things that cross the boundary from user input into dexter and are
  built once. `Registration` is validated, so bad wiring is rejected at registration time
  with a readable message.
- **Slotted classes and tuples** for things dexter builds for itself on the resolution path.
  A pydantic model costs roughly eight times a slotted class to construct, which is material
  when one is created per resolution step.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from dexter.commons import describe_type

type Key[T] = type[T] | Callable[..., T]
"""What a dependency is looked up by.

The `Callable` arm is what lets an abstract class or a `Protocol` be used as a key without
consumers having to suppress mypy's `type-abstract` error — a class object satisfies it
because calling it produces an instance.
"""

type Provider[T] = type[T] | Callable[..., T] | Callable[..., Awaitable[T]]
"""What produces a dependency: a class, a factory, or an async factory."""

type Dispose[T] = Callable[[T], None] | Callable[[T], Awaitable[None]]
"""What releases a dependency when its container closes.

Takes the instance, so an unbound method is usually enough: `dispose=Pool.aclose`. It may be
synchronous or asynchronous; an awaitable result is awaited.
"""


class Scope(StrEnum):
    """How long a resolved instance lives.

    There is no default. Lifetime is the most consequential property of a binding, so
    `Binder.to` requires it explicitly rather than letting it be chosen by omission.
    """

    TRANSIENT = "TRANSIENT"
    """A new instance for every resolution. Never cached."""

    SINGLETON = "SINGLETON"
    """One instance for the whole container graph, cached on the root container."""

    SCOPED = "SCOPED"
    """One instance per scope, cached on the scope that resolved it."""


class ParameterKind(StrEnum):
    """How a constructor parameter is satisfied."""

    DEPENDENCY = "DEPENDENCY"
    """Resolved from the container. An eager edge for cycle detection."""

    OPTIONAL = "OPTIONAL"
    """Annotated `X | None`: resolved if possible, otherwise `None`."""

    CONTAINER = "CONTAINER"
    """The resolving container itself. Not an edge — nothing is recursed into."""


class Registration(BaseModel):
    """One binding: a key, the provider that satisfies it, and its lifetime.

    Deliberately not generic. A generic `Registration[T]` would compile its `type[T]` field
    into an `issubclass` check, which raises `TypeError` for a `Protocol` that is not
    `runtime_checkable` — and most useful keys are protocols. `type[Any]` still rejects
    non-types and instances, which is the validation that matters.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    key: type[Any]
    provider: type[Any] | Callable[..., Any]
    scope: Scope
    is_async: bool = False
    instance: Any = None
    """Pre-built instance for `Binder.to_instance`; `None` for provider bindings."""

    has_instance: bool = False
    """Whether `instance` holds a pre-built value, since that value may itself be `None`."""

    dispose: Callable[[Any], Any] | None = None
    """Called with the instance when the owning container closes; `None` to release nothing."""


class PlannedParameter:
    """One constructor parameter, classified once when the binding is registered."""

    __slots__ = ("has_default", "key", "kind", "name")

    def __init__(
        self,
        name: str,
        kind: ParameterKind,
        key: type[Any] | None,
        *,
        has_default: bool,
    ) -> None:
        """Record one parameter's name, how it is satisfied, and its key if it has one."""
        self.name = name
        self.kind = kind
        self.key = key
        self.has_default = has_default


class DependencyPlan:
    """How to construct one provider, worked out once and reused for every resolution."""

    __slots__ = ("parameters",)

    def __init__(self, parameters: tuple[PlannedParameter, ...]) -> None:
        """Record the parameters to inject, in the order the constructor declares them."""
        self.parameters = parameters


class ResolutionStep:
    """One hop in a resolution, used to render a failure's path."""

    __slots__ = ("key", "parameter")

    def __init__(self, key: object, parameter: str | None = None) -> None:
        """Record a key and, if it was reached from one, the parameter that led to it."""
        self.key = key
        self.parameter = parameter


def describe_scope(scope: Scope) -> str:
    """Render a scope as the symbol a caller would type, such as `Scope.SINGLETON`.

    Interpolating the member directly would print only its bare value, because `StrEnum.__str__`
    returns the value. In a developer-facing message the qualified symbol is more useful: it is
    exactly what the reader has to write in their wiring.
    """
    return f"Scope.{scope.name}"


class ResolutionChain:
    """The path taken to reach the dependency currently being resolved.

    Immutable: `extend` returns a new chain. `eager_keys` drives cycle detection and is
    reset at a lazy boundary, because laziness is exactly what breaks a cycle. `depth`
    keeps counting across those boundaries so a lazy cycle still terminates.
    """

    __slots__ = ("depth", "eager_keys", "steps")

    MAX_DEPTH = 200
    """Generous ceiling; real graphs are an order of magnitude shallower."""

    def __init__(
        self,
        steps: tuple[ResolutionStep, ...] = (),
        eager_keys: frozenset[object] = frozenset(),
        depth: int = 0,
    ) -> None:
        """Create a chain; the default arguments give the empty chain for a fresh resolve."""
        self.steps = steps
        self.eager_keys = eager_keys
        self.depth = depth

    def extend(self, step: ResolutionStep) -> ResolutionChain:
        """Return a new chain with `step` appended and its key marked as eager."""
        return ResolutionChain(
            steps=(*self.steps, step),
            eager_keys=self.eager_keys | {step.key},
            depth=self.depth + 1,
        )

    def contains_eager(self, key: object) -> bool:
        """Whether `key` is already being constructed further up this chain."""
        return key in self.eager_keys

    def render(self) -> str:
        """Render the chain as an indented path, or an empty string if there is none."""
        if not self.steps:
            return ""
        lines = ["resolution chain:"]
        for index, step in enumerate(self.steps):
            indent = "  " * index
            arrow = "" if index == 0 else "-> "
            suffix = (
                "" if step.parameter is None else f" (parameter {step.parameter!r})"
            )
            lines.append(f"{indent}{arrow}{describe_type(step.key)}{suffix}")
        return "\n".join(lines)
