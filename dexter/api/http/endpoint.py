"""One route: the coroutine the framework calls, and the lifetime of one request.

The ordering in `_serve` is the whole design, so it is worth reading as a sequence:

1. The framework routes, coerces and validates. A malformed request never reaches this
   function — no scope is opened for it, and there is nothing to release.
2. The context is built from the transport's request, and bound to this task.
3. **The request scope opens.** The handler, its dependencies, and every middleware are
   resolved from it, so they all share one `RequestContext` and one set of scoped services.
4. **The scope closes — before the response is produced.** Its `dispose=` callbacks run in
   reverse creation order. An application that also wired `dexter.cqrs` settles its buses
   here, so a command the handler dispatched and never redeemed has completed, and the event
   it published has reached every reaction, before the caller is told anything at all.
5. Only then is the result returned for serialisation.

Step 4 preceding step 5 is why the scope is opened here rather than in a framework dependency
or an ASGI middleware. A dependency's teardown runs outside this function's `try`, so a
failure while settling would bypass the error map entirely; ASGI middleware runs before
routing, so it would open a scope for every 404 and every preflight and could not know which
handler was about to run.

**Streaming is therefore not supported.** A streaming response produces its body after this
function returns, by which time the scope is closed and anything the generator resolves from
it raises. Declining that outright is better than supporting it halfway.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.responses import JSONResponse

from dexter.dependency_injection import Container

from ..context import RequestContext, bind_request
from ..exposure import HttpExposure
from ..models import ErrorResponse, Invocation
from ..pipeline import ApiPipeline
from ..registry import ErrorMap, ExposureRecord
from .request import context_from
from .signature import HTTP_REQUEST, HTTP_RESPONSE, build_assembler, build_signature

PROBLEM_JSON = "application/problem+json"
"""Media type for a mapped failure, per RFC 9457."""


class _Route:
    """Everything about one route that is settled before the first request.

    Worked out once when the application is built rather than per request: reading a handler's
    contract and synthesising a signature are introspection, and introspection on the request
    path is a cost paid forever.
    """

    __slots__ = ("assemble", "container", "exposure", "record")

    def __init__(
        self,
        container: Container,
        record: ExposureRecord,
        exposure: HttpExposure,
    ) -> None:
        """Record the container to scope per request, the handler, and the way in."""
        self.container = container
        self.record = record
        self.exposure = exposure
        self.assemble = build_assembler(record.request_model, exposure)


def build_endpoint(
    container: Container, record: ExposureRecord, exposure: HttpExposure, /
) -> Callable[..., Awaitable[Any]]:
    """Return the coroutine the framework calls for one route."""
    route = _Route(container, record, exposure)
    signature, _ = build_signature(record.request_model, exposure, (Request, Response))

    async def endpoint(**bound: Any) -> Any:
        http_request: Request = bound.pop(HTTP_REQUEST)
        http_response: Response = bound.pop(HTTP_RESPONSE)
        context = context_from(http_request, record.handler)

        try:
            with bind_request(context):
                result = await _serve(route, context, bound)
        except Exception as error:
            return await _fail(container, error)

        _apply(context, exposure, http_response)
        return result

    # An `Any`-typed local rather than a `# type: ignore`, which `warn_unused_ignores` would
    # flag the day typeshed grows this attribute.
    target: Any = endpoint
    target.__signature__ = signature
    endpoint.__name__ = _name(record)
    endpoint.__doc__ = record.handler.__doc__
    return endpoint


async def _serve(
    route: _Route, context: RequestContext, bound: dict[str, Any], /
) -> Any:
    """Build the request, run it through the pipeline, and settle the scope."""
    try:
        request = route.assemble(bound)
    except ValidationError as error:
        # Raised in the framework's own shape, so a caller cannot tell a rule declared on the
        # request model from one the framework checked itself.
        raise RequestValidationError(error.errors()) from error

    async with route.container.scope() as scope:
        pipeline = await scope.resolve(ApiPipeline)
        invocation = Invocation(
            request=request,
            context=context,
            handler=route.record.handler,
            exposure=route.exposure,
        )

        async def handle(reached: Invocation) -> Any:
            """The terminal of the pipeline: construct the handler, and run it.

            Constructing here rather than before the pipeline is what makes a middleware's
            refusal cheap — a request turned away never builds the handler or anything the
            handler depends on.
            """
            handler = await scope.resolve(reached.handler)
            result: Any = await handler.handle(reached.request)
            return result

        return await pipeline.run(scope, invocation, handle)


async def _fail(container: Container, error: Exception) -> Response:
    """Turn a failure into a response, or let it through untouched.

    Three things are deliberately not caught. The framework's own exceptions are its business
    and the consumer may have replaced their handlers. And an exception nobody mapped is
    re-raised rather than tidied into a 500 here: the framework's error handling, the
    consumer's own exception handlers, and every logging integration all depend on seeing it.
    A library that turns each of its consumers' bugs into a neat response is a library that
    hides them.
    """
    if isinstance(error, HTTPException | RequestValidationError):
        # Checked explicitly rather than left to go unmapped, so that a consumer who maps
        # something broad cannot accidentally swallow the framework's own signalling.
        raise error

    mapping = (await container.resolve(ErrorMap)).find(error)
    if mapping is None:
        raise error

    body = ErrorResponse.of(mapping.status, mapping.title, error)
    return JSONResponse(
        body.model_dump(), status_code=int(mapping.status), media_type=PROBLEM_JSON
    )


def _apply(
    context: RequestContext, exposure: HttpExposure, response: Response, /
) -> None:
    """Move the handler's response directives onto the framework's temporary response.

    The framework merges what is set here into the real response while still serialising the
    returned value through the declared response model — which is how a handler sets a header
    or a status without giving up its schema.
    """
    status = context.status
    if status is not None and status != exposure.status:
        response.status_code = int(status)
    for name, value in context.response_headers():
        response.headers.append(name, value)
    for cookie in context.response_cookies():
        response.set_cookie(
            cookie.name,
            cookie.value,
            max_age=cookie.max_age,
            path=cookie.path,
            domain=cookie.domain,
            secure=cookie.secure,
            httponly=cookie.http_only,
            samesite=cookie.same_site,
        )


def _name(record: ExposureRecord, /) -> str:
    """A route name derived from the handler's own name."""
    name = record.handler.__name__
    trimmed = name.removesuffix("Handler") or name
    return "".join(
        f"_{letter.lower()}" if letter.isupper() and index else letter.lower()
        for index, letter in enumerate(trimmed)
    )
