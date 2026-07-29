"""Reading the transport's request into a `RequestContext`.

The one place in dexter that touches a framework request object, and it does exactly one
thing: copy what the caller sent into a type that names no transport. Everything downstream —
handlers, middleware, and any service they depend on — sees only the copy.

That is the structural fix for the failure this module exists to avoid. Where a framework
reconstructs a narrow request model and drops the transport's own request before the handler
runs, headers and cookies become unreachable and the only way to pass a caller's identity
inward is an ambient global. Copying them into the context first means nothing is lost, and
nothing has to be smuggled.
"""

from typing import Any

from starlette.requests import Request

from ..context import Headers, QueryValues, RequestContext


def context_from(request: Request, handler: type[Any], /) -> RequestContext:
    """Build the context for one request.

    Args:
        request: The transport's request.
        handler: The handler class being invoked, which is the operation's identity — a
            logging or authorisation middleware keys on it without knowing any URL.
    """
    client = request.client
    return RequestContext(
        handler=handler,
        method=request.method,
        path=request.url.path,
        url=str(request.url),
        scheme=request.url.scheme,
        headers=Headers(request.headers.items()),
        cookies=request.cookies,
        query=QueryValues(request.query_params.multi_items()),
        path_params={key: str(value) for key, value in request.path_params.items()},
        client_host=None if client is None else client.host,
        client_port=None if client is None else client.port,
    )
