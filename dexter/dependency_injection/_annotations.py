"""Provider introspection: turning a class or factory into a `DependencyPlan`.

Private to this module. Planning happens once, when a binding is registered, so malformed
constructors are reported while wiring rather than on first resolution.

Every rule here exists because the obvious version of it is wrong; see the notes on each.
"""

import functools
import inspect
import types
from annotationlib import Format
from collections.abc import Callable
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .errors import (
    InvalidRegistrationError,
    PositionalOnlyParameterError,
    UnresolvableParameterError,
    UnresolvedAnnotationError,
)
from .models import (
    DependencyPlan,
    ParameterKind,
    PlannedParameter,
    ResolutionChain,
    describe_key,
)

_REJECTED_KINDS = frozenset(
    {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
)


def is_protocol(candidate: object) -> bool:
    """Whether `candidate` is a `Protocol` class rather than something constructible."""
    return bool(getattr(candidate, "_is_protocol", False))


def is_async_provider(provider: object) -> bool:
    """Whether calling `provider` returns an awaitable.

    `inspect.iscoroutinefunction` sees `async def` and any depth of `functools.partial`
    wrapping it, but not a class whose `__call__` is async — hence the second check. A plain
    function that returns an awaitable is undetectable without calling it; it is classified
    as synchronous and rejected at resolution time with a message telling the author to use
    `async def`.

    `asyncio.iscoroutinefunction` is deliberately not used: it is deprecated in 3.14 and its
    warning is fatal under this project's warning filter.
    """
    if inspect.iscoroutinefunction(provider):
        return True
    if not isinstance(provider, type) and callable(provider):
        return inspect.iscoroutinefunction(type(provider).__call__)
    return False


def _construction_target(provider: object) -> Callable[..., Any] | None:
    """Return the callable whose parameters are the provider's dependencies.

    Order matters, and each branch is load-bearing:

    - A `Protocol` reports an `__init__` that only raises, so it must be rejected outright.
    - A non-class provider is a factory: its own signature is what we inject into.
    - `cls.__init__ is not object.__init__` is the correct "has a constructor" test.
      Checking `"__init__" in vars(cls)` instead would report `False` for a subclass that
      inherits a constructor with real dependencies, silently dropping every one of them.
    - Falling back to `__new__` catches `NamedTuple` and anything else that constructs there,
      where `__init__` really is `object.__init__`.
    """
    if is_protocol(provider):
        return None
    if not isinstance(provider, type):
        return provider if callable(provider) else None

    # Fetched through `getattr` into `Any`-typed locals on purpose: mypy rejects reading
    # `__init__`/`__new__` off a `type` as an unsound instance access, and treats the
    # identity comparisons below as non-overlapping. The comparisons are exactly right at
    # runtime, which is what matters here.
    initializer: Any = getattr(provider, "__init__", None)
    if initializer is not None and initializer is not object.__init__:
        target: Callable[..., Any] = initializer
        return target
    constructor: Any = getattr(provider, "__new__", None)
    if constructor is not None and constructor is not object.__new__:
        allocator: Callable[..., Any] = constructor
        return allocator
    return None


def _unwrap_partial(target: object) -> object:
    """Follow `functools.partial` wrappers down to the underlying function.

    `get_type_hints` on a partial object silently returns `{}`, so hints must come from the
    innermost function while parameters come from the partial itself.
    """
    while isinstance(target, functools.partial):
        target = target.func
    return target


def _type_hints(target: object, provider: object) -> dict[str, Any]:
    """Resolve `target`'s annotations, tolerating names that do not exist.

    `Format.FORWARDREF` resolves what it can and leaves the rest as `ForwardRef`, so an
    unresolvable dependency becomes a precise error instead of a `NameError` escaping from
    introspection. Note `Format` comes from `annotationlib`, not `typing`.

    `typing.get_type_hints` is used rather than `inspect.get_annotations`, which returns a
    bare string instead of a `ForwardRef` for unresolvable names and evaluates only one level
    of stringised annotation.

    `include_extras=True` keeps `Annotated` metadata, which is stripped explicitly below so
    the metadata stays available for future keyed registrations.
    """
    hint_source = _unwrap_partial(target)
    try:
        hints = get_type_hints(
            hint_source,
            format=Format.FORWARDREF,
            include_extras=True,
        )
    except (NameError, TypeError, AttributeError) as error:
        raise InvalidRegistrationError(
            f"could not read annotations for {describe_key(provider)}: {error}"
        ) from error
    hints.pop("return", None)
    return hints


def _signature(target: Callable[..., Any], provider: object) -> inspect.Signature:
    """Return `target`'s signature, or fail with a dexter error.

    `annotation_format=Format.FORWARDREF` is essential, not cosmetic: by default
    `inspect.signature` evaluates annotations eagerly and raises `NameError` for a name that
    does not exist, which would defeat the whole point of reading hints in forward-reference
    mode. Only parameter names, kinds and defaults are taken from here — the types come from
    `_type_hints` — but the call still has to survive a dangling annotation.

    Some builtins have no introspectable signature at all, which surfaces as `ValueError`.
    """
    try:
        return inspect.signature(target, annotation_format=Format.FORWARDREF)
    except (ValueError, TypeError, NameError) as error:
        raise InvalidRegistrationError(
            f"cannot inspect the signature of {describe_key(provider)}: {error}"
        ) from error


def _strip_annotated(hint: Any) -> Any:
    """Reduce `Annotated[X, ...]` to `X`, leaving anything else untouched."""
    if get_origin(hint) is Annotated:
        return get_args(hint)[0]
    return hint


def _optional_target(hint: Any) -> Any | None:
    """Return `X` for a hint meaning `X | None`, otherwise `None`.

    Only `X | None` counts. A wider union such as `A | B | None` is rejected because there is
    no defensible order in which to try its arms — and the reference implementation's attempt
    at one had no real callers and swallowed every exception to do it.
    """
    if get_origin(hint) not in (Union, types.UnionType):
        return None
    args = get_args(hint)
    if type(None) not in args:
        return None
    present = [arg for arg in args if arg is not type(None)]
    if len(present) != 1:
        return None
    return _strip_annotated(present[0])


def build_plan(provider: object, container_type: type[Any]) -> DependencyPlan:
    """Work out how to construct `provider`.

    `container_type` is injected rather than imported to keep this module free of a cycle
    back to `container`.
    """
    target = _construction_target(provider)
    if target is None:
        if is_protocol(provider):
            raise InvalidRegistrationError(
                f"{describe_key(provider)} is a Protocol and cannot be constructed; "
                f"bind a concrete implementation or a factory instead."
            )
        return DependencyPlan(parameters=())

    # Parameters come from the provider itself so that `self` is dropped and a
    # `functools.partial`'s already-bound arguments are excluded.
    signature_source = provider if isinstance(provider, type) else target
    signature = _signature(signature_source, provider)
    hints = _type_hints(target, provider)

    parameters: list[PlannedParameter] = []
    empty = inspect.Parameter.empty
    for parameter in signature.parameters.values():
        if parameter.name in ("self", "cls", "_cls"):
            continue
        if parameter.kind in _REJECTED_KINDS:
            raise UnresolvableParameterError(
                f"{describe_key(provider)} takes {parameter.name!r} as "
                f"*args/**kwargs, which cannot be injected.",
                ResolutionChain(),
            )
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise PositionalOnlyParameterError(
                f"{describe_key(provider)} takes {parameter.name!r} as a "
                f"positional-only parameter; dependencies are passed by keyword.",
                ResolutionChain(),
            )

        has_default = parameter.default is not empty
        hint = hints.get(parameter.name, empty)
        if hint is empty:
            if has_default:
                # Nothing to resolve and a value already exists: leave it to the default.
                continue
            raise UnresolvableParameterError(
                f"{describe_key(provider)} takes {parameter.name!r} with no annotation, "
                f"so it cannot be resolved.",
                ResolutionChain(),
            )

        hint = _strip_annotated(hint)
        if isinstance(hint, ForwardRef):
            raise UnresolvedAnnotationError(
                f"{describe_key(provider)} annotates {parameter.name!r} as "
                f"{hint.__forward_arg__!r}, which does not exist at runtime.",
                ResolutionChain(),
            )

        optional = _optional_target(hint)
        if optional is not None:
            parameters.append(
                PlannedParameter(
                    parameter.name,
                    ParameterKind.Optional,
                    optional,
                    has_default=has_default,
                )
            )
        elif hint is container_type:
            parameters.append(
                PlannedParameter(
                    parameter.name,
                    ParameterKind.Container,
                    None,
                    has_default=has_default,
                )
            )
        else:
            parameters.append(
                PlannedParameter(
                    parameter.name,
                    ParameterKind.Dependency,
                    hint,
                    has_default=has_default,
                )
            )

    return DependencyPlan(parameters=tuple(parameters))
