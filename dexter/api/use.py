"""Wiring: how an API is registered into a container.

The same two shapes every dexter module uses. `use_api(builder)` registers what the *module*
provides — the exposure registry, the middleware pipeline, the error map, and the per-request
context — and takes no configuration. `register_*(builder, ...)` registers what the
*application* contributes, once per handler, middleware or mapped exception. `use_api` must
run first: the registries a `register_*` writes into are the ones it creates.

    builder = ContainerBuilder()
    use_api(builder)
    register_handler(
        builder,
        GetRoomHandler,
        HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}"),
        scope=Scope.TRANSIENT,
    )
    container = builder.build()

    app = await create_app(container)     # from dexter.api.http

Nothing here knows about HTTP. `create_app` reads the same registry a second protocol would.
"""

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel

from dexter.commons import describe_type
from dexter.dependency_injection import (
    ContainerBuilder,
    InvalidRegistrationError,
    Scope,
)

from .context import RequestContext, current_request
from .errors import ApiNotWiredError
from .exposure import Exposure
from .models import ApiHandler, ApiMiddleware
from .pipeline import ApiPipeline
from .registry import ErrorMap, ExposureRegistry


def use_api(builder: ContainerBuilder) -> None:
    """Register the exposure registry, the pipeline, the error map, and the request context.

    Call once, before registering anything.

    The first three are bound as instances so the `register_*` functions can populate them
    while wiring, before the container is built.

    `RequestContext` is `Scope.SCOPED`, and that is three guarantees rather than a preference.
    Resolving it from the root raises `ScopeRequiredError`, because the root is not a request.
    `ContainerBuilder.build()` rejects any `Scope.SINGLETON` service that reaches it, with
    `CaptiveDependencyError` naming the path — so a process-wide service cannot quietly
    capture one request's caller and serve it to everyone else. And within one request every
    handler and middleware sees the same object.

    Nothing here is disposed. The per-request scope is opened by whichever transport is
    serving, and anything that needs settling as it closes — a CQRS bus, a unit of work —
    settles through its own `dispose=`. That is precisely why this module needs no knowledge
    of any other.
    """
    builder.register(ExposureRegistry).to_instance(ExposureRegistry())
    builder.register(ApiPipeline).to_instance(ApiPipeline())
    builder.register(ErrorMap).to_instance(ErrorMap())
    builder.register(RequestContext).to(current_request, scope=Scope.SCOPED)


def register_handler[TRequest: BaseModel, TResponse](
    builder: ContainerBuilder,
    handler: type[ApiHandler[TRequest, TResponse]],
    /,
    *exposures: Exposure,
    scope: Scope,
) -> None:
    """Bind `handler` and record every way it can be reached.

    One call per handler, however many exposures — passing them all together is what keeps the
    container binding unambiguous, and a second call for the same class raises
    `DuplicateExposureError` because its `scope=` would then be in question.

    Args:
        builder: The builder `use_api` was called on.
        handler: A class with one `async def handle(self, request)` taking a pydantic model.
        exposures: At least one `Exposure`. `HttpExposure` is the one dexter serves today.
        scope: The handler's lifetime. `Scope.TRANSIENT` is usual — a handler holding no state
            between requests wants a fresh instance each time.

    Every `{name}` in an HTTP path must be a field of the handler's request model; those
    fields are read from the path, and the rest from the body or the query string depending on
    the method. The summary and description default to the handler's own docstring, so one
    text documents it for a reader and in the schema.
    """
    _fetch(builder, ExposureRegistry).register(handler, exposures)
    _bind(builder, handler, scope)


def register_api_middleware(
    builder: ContainerBuilder,
    middleware: type[ApiMiddleware],
    /,
    *,
    scope: Scope,
) -> None:
    """Append `middleware` to the pipeline every request runs through.

    Order is registration order, outermost first: the first registered sees a request before
    every other one, and sees its response after every other one.

    Named `register_api_middleware` rather than `register_middleware` because `dexter.cqrs`
    already exports the latter, and an application wiring both imports them into one file.
    """
    _fetch(builder, ApiPipeline).add(middleware)
    _bind(builder, middleware, scope)


def register_error(
    builder: ContainerBuilder,
    error: type[Exception],
    /,
    *,
    status: HTTPStatus,
    title: str | None = None,
) -> None:
    """Map `error`, and every subclass of it, to `status`.

    Nothing is bound in the container, so there is no `scope=` to choose: an exception class
    is a key in a table, not a dependency. `scope=` is required on a `register_*` that binds a
    provider, and this one does not.

    Mapping an exception is itself the decision that its message may be shown — the response's
    `detail` is `str(error)`. An exception nobody maps never reaches this, and is reported as
    the unhandled failure it is.
    """
    _fetch(builder, ErrorMap).register(error, status=status, title=title)


def _bind(builder: ContainerBuilder, target: type[Any], scope: Scope) -> None:
    """Bind a handler or middleware as its own key, so a transport can resolve it by class.

    Always called *after* the registry has accepted the registration, so that a malformed or
    duplicate declaration is reported by the precise API error rather than by whichever of the
    container's own guards happened to trip first.
    """
    builder.register(target).to(target, scope=scope)


def _fetch[T](builder: ContainerBuilder, key: type[T]) -> T:
    """Fetch a registry from the builder, or explain that wiring is missing."""
    try:
        return builder.resolve_instance(key)
    except InvalidRegistrationError as error:
        raise ApiNotWiredError(
            f"{describe_type(key)} is not registered, so there is nothing to register into. "
            f"Call `use_api(builder)` before registering handlers, middleware or errors."
        ) from error
