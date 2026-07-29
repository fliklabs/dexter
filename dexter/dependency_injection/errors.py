"""Exceptions raised by the dependency injection module.

Resolution failures carry the path taken to reach them. `args` stays the short message so
`pytest.raises(match=...)` and log lines remain one-liners, while `str(exc)` appends the
rendered chain.
"""

from dexter.commons import DexterError, DexterGroupError, describe_type

from .models import ResolutionChain, Scope, describe_scope


class DependencyInjectionError(DexterError):
    """Base class for every dependency injection failure.

    Consumers can catch this to cover both wiring and resolution problems.
    """


# ── registration ─────────────────────────────────────────────────────


class RegistrationError(DependencyInjectionError):
    """A binding could not be recorded. Raised while wiring, before any resolution."""


class DuplicateRegistrationError(RegistrationError):
    """The key is already bound.

    Rebinding is not silently permitted, because the winner would depend on the order in
    which unrelated wiring functions happened to run.
    """


class IncompleteRegistrationError(RegistrationError):
    """`register(key)` was never completed with `.to(...)` or `.to_instance(...)`."""


class InvalidRegistrationError(RegistrationError):
    """The key or provider cannot be used.

    Covers a non-class key, a provider that is not callable, a `Protocol` used as a
    provider, and a provider classified as synchronous that returned an awaitable.
    """


class CaptiveDependencyError(RegistrationError):
    """A `Scope.SINGLETON` binding depends, transitively, on a `Scope.SCOPED` one.

    A singleton outlives every scope, so there is no scope whose instance it could
    legitimately hold: whichever it captured first would be shared by every later scope for
    the lifetime of the process. Raised by `ContainerBuilder.build`, so the mistake surfaces
    while wiring rather than as inexplicably shared state at runtime.

    Rebind the dependent as `Scope.SCOPED`, or take a `Container` parameter and resolve the
    scoped dependency when it is needed rather than holding it.
    """

    def __init__(self, message: str, path: tuple[str, ...]) -> None:
        """Record the message and the rendered dependency path that reaches the scoped key."""
        super().__init__(message)
        self.path = path

    def __str__(self) -> str:
        """Render the message followed by the offending dependency path."""
        message = super().__str__()
        if not self.path:
            return message
        steps = "\n".join(
            f"{'  ' * index}{'' if index == 0 else '-> '}{step}"
            for index, step in enumerate(self.path)
        )
        return f"{message}\n\n{steps}"


# ── resolution ───────────────────────────────────────────────────────


class ResolutionError(DependencyInjectionError):
    """A dependency could not be produced. Always carries the resolution chain."""

    def __init__(self, message: str, chain: ResolutionChain) -> None:
        """Record the short message and the path taken to reach the failure."""
        super().__init__(message)
        self.chain = chain

    def __str__(self) -> str:
        """Render the message followed by the resolution path, when there is one."""
        message = super().__str__()
        rendered = self.chain.render()
        if not rendered:
            return message
        return f"{message}\n\n{rendered}"


class UnregisteredDependencyError(ResolutionError):
    """No binding exists for the requested key.

    dexter never constructs an unregistered class. Implicit construction turns a typo into a
    silently-created object, and gives hot-path types an accidental lifetime.
    """

    def __init__(self, key: object, chain: ResolutionChain) -> None:
        """Name the unregistered key and record how it was reached."""
        super().__init__(
            f"{describe_type(key)} is not registered in this container.", chain
        )
        self.key = key


class ScopeRequiredError(ResolutionError):
    """A `Scope.SCOPED` key was resolved from a container that is not a scope.

    `Scope.SCOPED` means one instance per scoped container, and the root is not one — so there
    is no instance to hand back. Caching it on the root instead would quietly turn it into a
    singleton shared by the whole process, which is the trap this replaces.

    Resolve it inside `async with container.scope() as scope:` instead.
    """

    def __init__(self, key: object, chain: ResolutionChain) -> None:
        """Name the scoped key that was asked for outside any scope."""
        super().__init__(
            f"{describe_type(key)} is registered as {describe_scope(Scope.SCOPED)} and can "
            f"only be resolved inside a scope; this container is the root. "
            f"Use `async with container.scope() as scope:` and resolve from the scope.",
            chain,
        )
        self.key = key


class CircularDependencyError(ResolutionError):
    """A key depends on itself through eager constructor parameters."""

    def __init__(self, key: object, chain: ResolutionChain) -> None:
        """Name the key that closes the cycle and record the path that revealed it."""
        super().__init__(
            f"circular dependency detected: {describe_type(key)} depends on itself.",
            chain,
        )
        self.key = key


class ResolutionDepthExceededError(ResolutionError):
    """Resolution nested deeper than `ResolutionChain.MAX_DEPTH`.

    A backstop for cycles that eager detection cannot see, so the failure is a dexter error
    naming the graph rather than a bare `RecursionError`.
    """

    def __init__(self, key: object, chain: ResolutionChain) -> None:
        """Name the key being resolved when the depth ceiling was hit."""
        super().__init__(
            f"resolution exceeded {ResolutionChain.MAX_DEPTH} levels while resolving "
            f"{describe_type(key)}; the dependency graph is probably cyclic.",
            chain,
        )
        self.key = key


class UnresolvableParameterError(ResolutionError):
    """A constructor parameter cannot be injected.

    Raised for an unannotated parameter and for `*args` / `**kwargs`, neither of which
    carries enough information to resolve.
    """


class UnresolvedAnnotationError(ResolutionError):
    """A parameter's annotation names something that does not exist at runtime.

    Reported instead of letting a `NameError` escape from deep inside introspection.
    """


class PositionalOnlyParameterError(ResolutionError):
    """A constructor takes a positional-only parameter.

    dependencies are always passed by keyword, so a positional-only parameter cannot be
    satisfied. Passing positionally instead would silently shift arguments whenever a
    parameter is skipped.
    """


# ── lifecycle ────────────────────────────────────────────────────────


class ContainerStateError(DependencyInjectionError):
    """The container or a scope was used after it was closed."""


class DisposalError(DexterGroupError, DependencyInjectionError):
    """One or more `dispose=` callbacks failed while a container was closing.

    An `ExceptionGroup`, because closing releases every instance the container created and one
    failure must not stop the rest being released — or hide them. The container is closed
    either way: this reports what went wrong on the way out, it does not mean teardown was
    abandoned. Split it with `except*`.
    """


class ContainerClosedError(ContainerStateError):
    """The container is closed and can no longer resolve or create scopes."""


class ScopeClosedError(ContainerStateError):
    """The scope has exited and can no longer resolve."""
