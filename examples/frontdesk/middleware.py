"""Cross-cutting concerns, written once and applied to every way in.

API middleware sees an `Invocation` — the parsed request, the context, the handler and the
exposure — and never a transport type. That is what would let the same `RequireTenant` below
guard a second protocol without being rewritten.

It is not the same thing as the web framework's own middleware, and both have their place. The
framework's runs before routing on raw bytes, which is right for compression or CORS. This runs
after validation, inside the request's container scope, with its dependencies injected — which
is what makes it able to refuse a request on a domain rule.
"""

from http import HTTPStatus
from typing import Any

from dexter.api import ApiNext, Invocation

from .display import line
from .domain import Whoami


class RequireTenant:
    """Refuses a request that does not say who it is for.

    Refusing means returning something instead of calling `call_next` — the handler is never
    constructed and never runs. Raising a mapped exception is the other way; this one shows
    that a middleware can answer on its own.
    """

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        """Let the request through if it named a tenant, and answer it if it did not."""
        if invocation.context.headers.get("x-tenant") is None:
            invocation.context.set_status(HTTPStatus.UNAUTHORIZED)
            return Whoami(tenant="unknown", session=None, address=None, user_agent=None)
        return await call_next(invocation)


class Trace:
    """Prints one line as a request goes down, and another as it comes back.

    Registered first, so it wraps everything else — including `RequireTenant`, which is why a
    refused request still shows both halves.
    """

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        """Announce the request, run the rest of the pipeline, then announce the result."""
        name = invocation.handler.__name__
        line(f"-> {name}")
        result = await call_next(invocation)
        line(f"<- {name}")
        return result
