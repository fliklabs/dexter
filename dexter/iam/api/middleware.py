"""Turning an `Authorization` header into a caller, once per request.

The middleware reads the header, verifies the token, and leaves the result where anything in
the request's dependency graph can ask for it. Four decisions in it are worth stating, because
each is a fork where the obvious choice is wrong:

**It raises rather than returning.** `dexter/api/AGENTS.md` is explicit about this: a middleware
that spans many handlers and *returns* a value has that value serialised through the refused
route's response model, so refusing a request that was going to answer with a `RoomView`
produces a 500 about response validation instead of a 401. `use_authentication` maps the four
token errors to 401 so the raise renders as problem details.

**A bad token is refused even on an open route.** Absence and garbage are different: nobody
signed in is an ordinary state, and a token that will not verify is a caller doing something
wrong — usually a client that kept a token past a key rotation, which should be told so rather
than silently downgraded to anonymous on the one route that would have let it through.

**Nothing is constructed for a refused request.** `Invocation.handler` is the handler *class*,
so the decision is made before the container builds the handler or anything beneath it. A
request turned away costs no database connection.

**The principal is left in `context.state`, and read back through a binding.** Handlers do not
reach into the scratch space; they declare a `Principal` or an `Authentication` parameter and
the container supplies it. That is `dexter.api`'s own documented idiom, and it is what lets a
repository three levels down know who is asking without a web framework in its imports.
"""

from typing import Any

from dexter.api import ApiNext, Invocation, RequestContext

from ..errors import InvalidTokenError, NotAuthenticatedError
from ..models import Principal
from ..tokens import TokenService
from .registry import AuthenticationRegistry

HEADER = "authorization"
"""Where the token is read from. Lookup is case-insensitive, as HTTP requires."""

SCHEME = "Bearer"
"""The one authentication scheme understood. RFC 6750, and what every client already sends."""

STATE_KEY = "dexter.iam.authentication"
"""Where the outcome is left for the scoped bindings to read.

Namespaced, because `RequestContext.state` is shared with every other middleware in the
application and `"auth"` is a name two of them will eventually both want.
"""


class Authentication:
    """The outcome of authenticating one request.

    Always resolvable, on an open route as much as a closed one — which is what makes it the
    right parameter for code that behaves differently for a signed-in caller rather than
    refusing an anonymous one.

    Slotted rather than pydantic: dexter builds one per request out of a token it has already
    verified, and no consumer constructs one from untrusted input.
    """

    __slots__ = ("principal",)

    def __init__(self, principal: Principal | None = None) -> None:
        """Record who is calling, if anyone."""
        self.principal = principal

    @property
    def is_authenticated(self) -> bool:
        """Whether a caller was identified."""
        return self.principal is not None

    def require(self) -> Principal:
        """The caller, or `NotAuthenticatedError` if there is none.

        Raises:
            NotAuthenticatedError: If the request named nobody.
        """
        if self.principal is None:
            raise NotAuthenticatedError("this operation needs an authenticated caller.")
        return self.principal

    def __repr__(self) -> str:
        subject = self.principal.subject if self.principal else None
        return f"Authentication(subject={subject!r})"


class AuthenticationMiddleware:
    """Verifies the bearer token, and refuses a request the route will not serve anonymously.

    Registered first in the pipeline by convention, so that everything after it — auditing,
    rate limiting, anything reading `Authentication` — sees the outcome rather than racing it.
    """

    __slots__ = ("_registry", "_tokens")

    def __init__(self, registry: AuthenticationRegistry, tokens: TokenService) -> None:
        """Record the rules and the service that reads a token."""
        self._registry = registry
        self._tokens = tokens

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        """Authenticate the request, then continue — or refuse it before anything is built."""
        principal = self._principal(invocation.context)
        invocation.context.state[STATE_KEY] = Authentication(principal)

        if principal is None and self._registry.requires(invocation.handler):
            raise NotAuthenticatedError(
                "this route needs an access token; send one as "
                "`Authorization: Bearer <token>`."
            )
        return await call_next(invocation)

    def _principal(self, context: RequestContext, /) -> Principal | None:
        """Who the request's header says is calling, or `None` when it carries no token."""
        header = context.headers.get(HEADER)
        if header is None:
            return None
        return self._tokens.verify_access(_token_from(header))


def _token_from(header: str, /) -> str:
    """The credential out of an `Authorization` header value.

    The scheme is compared case-insensitively because RFC 7235 says it is case-insensitive, and
    clients disagree about how to spell it. Anything that is not `<scheme> <token>` is an
    invalid token rather than an absent one — the caller tried to authenticate and got it
    wrong, which is worth telling them.
    """
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != SCHEME.lower() or not credential.strip():
        raise InvalidTokenError(f"the {HEADER} header is not `{SCHEME} <token>`.")
    return credential.strip()


def current_authentication(context: RequestContext) -> Authentication:
    """The outcome of authenticating the request being served.

    This is the provider `use_authentication` binds `Authentication` to, which is why it is a
    plain function rather than a method on anything.

    An `Authentication` naming nobody is returned when the middleware has not run — a handler
    reached outside the pipeline, in a test that built the context by hand. Anonymous is the
    truthful answer there: nothing has authenticated anybody.
    """
    outcome = context.state.get(STATE_KEY)
    return outcome if isinstance(outcome, Authentication) else Authentication()


def current_principal(context: RequestContext) -> Principal:
    """The caller, or `NotAuthenticatedError` if the request named nobody.

    This is the provider `use_authentication` binds `Principal` to, and it makes the annotation
    do real work: a handler that declares a `Principal` parameter has *said* it needs one, and
    gets a 401 rather than a `None` to check. Code that tolerates anonymity declares an
    `Authentication` instead.
    """
    return current_authentication(context).require()
