"""Checking that a handler can actually serve what it is being registered for.

Private to this module. Every check here runs once, when the registration is recorded, so a
mistake is reported while wiring rather than as a 500 on the first request that reaches it.

Annotations are read with `typing.get_type_hints(..., format=Format.FORWARDREF)` and
parameters with `inspect.signature(..., annotation_format=Format.FORWARDREF)`, never raw
`__annotations__` — which are plain strings whenever the defining module uses
`from __future__ import annotations`. Forward-reference mode means an annotation naming
something that does not exist at runtime arrives as a `ForwardRef` this module can turn into a
precise error, rather than a `NameError` escaping from introspection.

The dict `get_type_hints` returns is **not** in signature order, so parameters are taken from
the signature and hints are looked up by name. Zipping them would silently mis-pair.
"""

import inspect
from annotationlib import Format
from typing import Any, ForwardRef, get_type_hints

from pydantic import BaseModel

from dexter.commons import describe_type

from .errors import InvalidApiHandlerError

_MISSING = object()


def check_shape(target: type[Any], noun: str) -> None:
    """Reject anything that is not a constructible class with an async `handle`.

    The container will construct `target`, so a `Protocol` is rejected here rather than
    surfacing later as the container's own "cannot be constructed" message, which names the
    type without saying which registration introduced it.

    Deliberately duplicated from the CQRS module rather than shared through `dexter.commons`.
    Two consumers is that package's floor, not its trigger, and the two checks are already
    diverging: this one goes on to require a request model, and that one requires a result
    matching the message. A shared helper would have to grow a flag for the difference.
    """
    if not isinstance(target, type):
        raise InvalidApiHandlerError(
            f"{target!r} cannot be a {noun}; a {noun} must be a class."
        )
    if getattr(target, "_is_protocol", False):
        raise InvalidApiHandlerError(
            f"{describe_type(target)} is a Protocol and cannot be constructed; "
            f"register a concrete implementation."
        )
    handle = getattr(target, "handle", None)
    if handle is None:
        raise InvalidApiHandlerError(
            f"{describe_type(target)} has no `handle` method, so it cannot be a {noun}."
        )
    if not inspect.iscoroutinefunction(handle):
        raise InvalidApiHandlerError(
            f"{describe_type(target)}.handle is not asynchronous. dexter is async-native; "
            f"declare it with `async def`."
        )


def read_contract(handler: type[Any], /) -> tuple[type[BaseModel], Any]:
    """Return the request model and the declared response type of `handler.handle`.

    Raises:
        InvalidApiHandlerError: If the shape is wrong, the annotations cannot be read, the
            request is not a pydantic model, or the response is not annotated.
    """
    check_shape(handler, "handler")
    parameter = _sole_parameter(handler)
    hints = _hints(handler)

    request = hints.get(parameter, _MISSING)
    if request is _MISSING:
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle does not annotate its {parameter!r} "
            f"parameter, so the request it accepts is unknown. Annotate it with a pydantic "
            f"model."
        )
    if isinstance(request, ForwardRef):
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle annotates {parameter!r} as "
            f"{request.__forward_arg__!r}, which does not resolve at runtime. The request "
            f"model has to be importable, not only visible to a type checker."
        )
    if not (isinstance(request, type) and issubclass(request, BaseModel)):
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle takes {describe_type(request)}, which is not "
            f"a pydantic model. A request must be one, so it can be validated and described "
            f"in a schema."
        )

    response = hints.get("return", _MISSING)
    if response is _MISSING:
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle has no return annotation, so what it serves "
            f"is unknown. Annotate it, using `-> None` when there is no body."
        )
    if isinstance(response, ForwardRef):
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle declares a return of "
            f"{response.__forward_arg__!r}, which does not resolve at runtime."
        )
    return request, response


def _sole_parameter(handler: type[Any], /) -> str:
    """The name of `handle`'s only parameter after `self`."""
    try:
        signature = inspect.signature(
            handler.handle, annotation_format=Format.FORWARDREF
        )
    except (TypeError, ValueError) as error:
        raise InvalidApiHandlerError(
            f"could not read the signature of {describe_type(handler)}.handle: {error}"
        ) from error

    names = [name for name in signature.parameters if name != "self"]
    if len(names) != 1:
        listed = ", ".join(names) if names else "nothing"
        raise InvalidApiHandlerError(
            f"{describe_type(handler)}.handle takes {listed}; a handler takes exactly one "
            f"request. Everything else about the invocation is injected — declare a "
            f"`RequestContext` constructor parameter for headers and cookies."
        )
    return names[0]


def _hints(handler: type[Any], /) -> dict[str, Any]:
    """The resolved annotations of `handle`, tolerating unresolvable names."""
    try:
        return get_type_hints(handler.handle, format=Format.FORWARDREF)
    except (NameError, TypeError, AttributeError) as error:
        raise InvalidApiHandlerError(
            f"could not read the annotations of {describe_type(handler)}.handle: {error}"
        ) from error
