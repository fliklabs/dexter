"""Checking that a handler actually handles the message it is being registered for.

Private to this module. Every check here runs once, when the binding is recorded, so a
mismatch is reported while wiring rather than as a wrong value at runtime.

Half of this could be a type check and half could not, which is why it exists. mypy rejects a
handler registered for the wrong *message*, because the message type appears in both arguments
of the registration call. It cannot reject a handler that returns the wrong *type*: expressing
that needs a type parameter bounded by another type parameter (`TCommand: Command[TResult]`),
which mypy does not support — and when two arguments constrain one variable it silently joins
them rather than reporting the conflict. So the result half is checked here.
"""

import inspect
from annotationlib import Format
from typing import Any, TypeVar, get_type_hints

from dexter.commons import describe_type

from .errors import (
    HandlerResultMismatchError,
    InvalidHandlerError,
    UnparameterizedMessageError,
)
from .models import Event

_MISSING = object()


def declared_result_type(message_type: type[Any], base: type[Any]) -> Any:
    """Return what `message_type` says its handler produces.

    pydantic turns `class CreateUser(Command[UserId])` into a real intermediate class holding
    `{"origin": Command, "args": (UserId,)}`, so the MRO is walked rather than
    `__orig_bases__` — which pydantic leaves pointing at `BaseModel` and `Generic[TResult]`,
    and which would report nothing useful for a subclass of a concrete command either.
    """
    for klass in message_type.__mro__:
        metadata = getattr(klass, "__pydantic_generic_metadata__", None)
        if metadata is None or metadata.get("origin") is not base:
            continue
        arguments = metadata.get("args")
        if not arguments:
            break
        # `Command[None]` records the literal `None`, while an annotation records `NoneType`.
        # Normalising here keeps the comparison below an identity check.
        return type(None) if arguments[0] is None else arguments[0]

    raise UnparameterizedMessageError(
        f"{describe_type(message_type)} does not say what it produces. Declare it as "
        f"{describe_type(base)}[Result], or {describe_type(base)}[None] when there is no "
        f"result."
    )


def handler_result_type(handler: type[Any]) -> Any:
    """Return the annotated return type of `handler.handle`.

    Read with `Format.FORWARDREF` so an annotation naming something that does not exist at
    runtime arrives as a `ForwardRef` to report, rather than a `NameError` escaping from
    introspection.
    """
    try:
        hints = get_type_hints(handler.handle, format=Format.FORWARDREF)
    except (NameError, TypeError, AttributeError) as error:
        raise InvalidHandlerError(
            f"could not read the annotations of {describe_type(handler)}.handle: {error}"
        ) from error
    return hints.get("return", _MISSING)


def check_shape(target: type[Any], noun: str) -> None:
    """Reject anything that is not a constructible class with an async `handle`.

    The container will construct `target`, so a `Protocol` is rejected here rather than
    surfacing later as the container's own "cannot be constructed" message, which would name
    the type without saying which registration introduced it.
    """
    if not isinstance(target, type):
        raise InvalidHandlerError(
            f"{target!r} cannot be a {noun}; a {noun} must be a class."
        )
    if getattr(target, "_is_protocol", False):
        raise InvalidHandlerError(
            f"{describe_type(target)} is a Protocol and cannot be constructed; "
            f"register a concrete implementation."
        )
    handle = getattr(target, "handle", None)
    if handle is None:
        raise InvalidHandlerError(
            f"{describe_type(target)} has no `handle` method, so it cannot be a {noun}."
        )
    if not inspect.iscoroutinefunction(handle):
        raise InvalidHandlerError(
            f"{describe_type(target)}.handle is not asynchronous. dexter is async-native; "
            f"declare it with `async def`."
        )


def _results_agree(declared: Any, returned: Any) -> bool:
    """Whether a handler returning `returned` satisfies a message declaring `declared`.

    A subclass is accepted, because returning something more specific than promised is always
    safe. Anything that is not a pair of classes — a union, a generic alias, `Any` — falls back
    to equality, which is the only comparison that means anything there.
    """
    if declared is Any or returned is Any:
        return True
    if isinstance(declared, TypeVar) or isinstance(returned, TypeVar):
        # A generic handler or message: there is nothing concrete to compare.
        return True
    if isinstance(declared, type) and isinstance(returned, type):
        return issubclass(returned, declared)
    return bool(declared == returned)


def validate_handler(
    message_type: type[Any], handler: type[Any], base: type[Any]
) -> None:
    """Reject a handler that cannot handle `message_type`.

    `base` is `Command`, `Query` or `Event`. For an event the handler must return `None`;
    for the other two it must return what the message declares.
    """
    check_shape(handler, "handler")
    returned = handler_result_type(handler)

    if returned is _MISSING:
        raise InvalidHandlerError(
            f"{describe_type(handler)}.handle has no return annotation, so it cannot be "
            f"checked against {describe_type(message_type)}. Annotate it."
        )

    declared = type(None) if base is Event else declared_result_type(message_type, base)
    if not _results_agree(declared, returned):
        raise HandlerResultMismatchError(message_type, declared, returned)
