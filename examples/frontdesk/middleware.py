"""Cross-cutting concerns, written once and applied to every way in.

API middleware sees an `Invocation` — the parsed request, the context, the handler and the
exposure — and never a transport type. That is what would let the same `RequireTenant` below
guard a second protocol without being rewritten.

It is not the same thing as the web framework's own middleware, and both have their place. The
framework's runs before routing on raw bytes, which is right for compression or CORS. This runs
after validation, inside the request's container scope, with its dependencies injected — which
is what makes it able to refuse a request on a domain rule.
"""

from typing import Any

from dexter.api import ApiNext, Invocation

from .display import line
from .domain import NotAuthenticatedError


class RequireTenant:
    """Refuses a request that does not say who it is for.

    **It refuses by raising, and that is the part worth copying.** A middleware can also refuse
    by returning something instead of calling `call_next`, and for a middleware that guards one
    handler that is fine. This one guards every route, and whatever it returns is still
    serialised through *that route's* declared response model — so returning, say, a `Whoami`
    would answer `GET /rooms` (which declares `list[str]`) with an object, and the framework
    would reject it as an invalid response rather than a refused request.

    Raising an exception that has been mapped to a status sidesteps that entirely: the mapping
    produces the response, so it fits every route the middleware covers no matter what each one
    returns.
    """

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        """Let the request through if it named a tenant, and refuse it if it did not."""
        if invocation.context.headers.get("x-tenant") is None:
            raise NotAuthenticatedError("requests must carry an X-Tenant header")
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
