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


class InvalidField(BaseModel):
    """One rejected field of a request.

    dexter's own shape rather than the validator's raw report: that carries a link to the
    validation library's documentation and an echo of the offending input, neither of which a
    caller needs, and it abbreviates the three things they do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    location: tuple[str | int, ...]
    """Where the field was read from, most general first: `("body", "nights")`.

    An integer indexes into a list, so `("body", "rooms", 0, "number")` is reachable.
    """

    message: str
    """What is wrong with it, in words."""

    kind: str
    """Which rule it broke, as a stable token a client can branch on."""


class ErrorResponse(BaseModel):
    """What a failure is serialised to — every failure, whatever raised it.

    RFC 9457 problem details: `title` for the class of failure, `status` for the code, `detail`
    for this particular occurrence, served as `application/problem+json`. Following the
    standard rather than inventing an envelope means a client that already understands one
    needs no special case for dexter, and using it for *every* error means they need only one
    parser rather than one per layer that might fail.

    `type` is deliberately absent, which the specification allows: when it is not present its
    value is assumed to be `about:blank`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    """What kind of failure this is. Stable across occurrences."""

    status: int
    """The HTTP status. Advisory, per the specification — the real one is on the response."""

    detail: str
    """What went wrong this time."""

    errors: tuple[InvalidField, ...] | None = None
    """Which fields were rejected, when that is what happened.

    An extension member, which the specification permits. `None` for every other kind of
    failure, and dropped from the JSON rather than serialised as null, so an ordinary error
    body carries exactly three fields.
    """

    @classmethod
    def of(
        cls,
        status: int,
        title: str | None,
        detail: str,
        errors: tuple[InvalidField, ...] | None = None,
    ) -> Self:
        """Build a body, defaulting the title to the status's standard phrase.

        Takes a plain `int`, not an `HTTPStatus`. A caller is free to answer with a code the
        standard does not name — some gateways and some internal conventions do — and the code
        they chose has to survive, so it is never rounded to one this module recognises.
        """
        return cls(
            title=title if title is not None else describe_status(status),
            status=status,
            detail=detail,
            errors=errors,
        )


def describe_status(status: int, /) -> str:
    """The standard phrase for `status`, or a neutral word when it names no standard one."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"
