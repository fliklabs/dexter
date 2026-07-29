"""The contracts: what a handler is, what a middleware is, and what they are handed.

Protocols, so nothing has to inherit from dexter to be a handler. A handler is an ordinary
class with one async method; the container constructs it, so its dependencies — including the
`RequestContext` for the request being served — are ordinary annotated constructor parameters.

A handler takes **one** argument, the request model, exactly as a CQRS handler takes one
message. Everything else about the invocation is injected rather than passed, which is what
keeps this signature identical whatever protocol the handler is exposed over.
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict

from .context import RequestContext
from .exposure import Exposure


class ApiHandler[TRequest, TResponse](Protocol):
    """Serves one request, over whatever protocol it is exposed on."""

    async def handle(self, request: TRequest) -> TResponse:
        """Answer `request`."""
        ...


class Invocation:
    """One request being served: what was asked, of what, and how it was reached.

    Slotted rather than pydantic: dexter builds one per request and no consumer constructs
    one, so validating it would be paying for nothing.

    Middleware sees this rather than a transport request or response, which is what lets one
    middleware apply to every exposure of every handler — including exposures over protocols
    that do not exist yet.

    `handler` is the handler **class**, not an instance, and that is deliberate: the handler is
    constructed only if the pipeline actually reaches it. A middleware that refuses a request
    without calling `call_next` therefore costs nothing to build the handler's dependency
    graph — which for a rejected request might otherwise mean opening a database connection to
    serve a caller who was never allowed in. Middleware wanting to make a policy decision
    wants the type anyway.
    """

    __slots__ = ("context", "exposure", "handler", "request")

    def __init__(
        self,
        *,
        request: Any,
        context: RequestContext,
        handler: type[Any],
        exposure: Exposure,
    ) -> None:
        """Record the parsed request, its context, the handler class, and the way in."""
        self.request = request
        self.context = context
        self.handler = handler
        self.exposure = exposure

    def __repr__(self) -> str:
        return (
            f"Invocation(request={type(self.request).__name__}, "
            f"handler={self.handler.__name__})"
        )


type ApiNext = Callable[[Invocation], Awaitable[Any]]
"""Continues the pipeline. Takes the invocation, so middleware can enrich it on the way down."""


class ApiMiddleware(Protocol):
    """Wraps every request, on every protocol.

    One protocol rather than one per transport, so a concern like authentication or auditing
    is written once. The result is `Any` at this boundary because middleware is generic over
    every handler, and there is no useful type to give the result of an arbitrary one.
    """

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        """Do something around `call_next(invocation)`, and return its result.

        Not calling `call_next` short-circuits the request, which is how an authorisation
        check refuses one: return a response, or raise an exception that has been mapped to a
        status.
        """
        ...


class ErrorResponse(BaseModel):
    """What a mapped failure is serialised to.

    Shaped after RFC 9457's problem details — `title` for the class of failure, `status` for
    the code, `detail` for this particular occurrence — and served as
    `application/problem+json`. Following the standard rather than inventing an envelope means
    clients that already understand one need no special case for dexter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    """What kind of failure this is. Stable across occurrences."""

    status: int
    """The HTTP status, repeated in the body as the specification requires."""

    detail: str
    """What went wrong this time."""

    @classmethod
    def of(cls, status: HTTPStatus, title: str | None, error: Exception) -> Self:
        """Build a body for `error`, defaulting the title to the status's own phrase."""
        return cls(
            title=title if title is not None else status.phrase,
            status=int(status),
            detail=str(error),
        )
