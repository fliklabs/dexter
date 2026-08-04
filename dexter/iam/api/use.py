"""Wiring: how authentication is put in front of an API.

The same two shapes every dexter module uses. `use_authentication(builder)` registers what the
*module* provides — the rule registry, the middleware, the two scoped bindings, and the error
mappings that make a refusal a 401 — and takes no configuration. `require_authentication` is
what the *application* contributes, once per handler it wants closed.

    use_api(builder)                      # or use_application(builder)
    use_iam(builder)
    use_authentication(builder)

    register_handler(builder, PickupApi, HttpExposure(...), scope=Scope.TRANSIENT)
    require_authentication(builder, PickupApi)

Three ordering rules, each with a precise failure:

- `use_api` before `use_authentication`, because the pipeline the middleware joins is created
  there. Reported as `ApiNotWiredError` by `register_api_middleware` itself.
- `use_iam` before `use_authentication` is *not* required — the container resolves
  `TokenService` when the first request arrives, not while wiring.
- `use_authentication` before every `require_authentication`, because the registry is created
  there. Reported here as `IamNotWiredError` naming the call.

**Register it first.** Order in the pipeline is registration order, outermost first, so calling
this before any other `register_api_middleware` is what makes "the auditing middleware sees who
is calling" true rather than accidental.
"""

from http import HTTPStatus
from typing import Any

from dexter.api import register_api_middleware, register_error
from dexter.commons import describe_type
from dexter.dependency_injection import (
    ContainerBuilder,
    InvalidRegistrationError,
    Scope,
)

from ..errors import (
    ExpiredTokenError,
    IamNotWiredError,
    InvalidTokenError,
    NotAuthenticatedError,
    WrongTokenKindError,
)
from ..models import Principal
from .middleware import (
    Authentication,
    AuthenticationMiddleware,
    current_authentication,
    current_principal,
)
from .registry import AuthenticationRegistry

UNAUTHORIZED = (
    (NotAuthenticatedError, "Not authenticated"),
    (InvalidTokenError, "Invalid token"),
    (ExpiredTokenError, "Expired token"),
    (WrongTokenKindError, "Wrong token kind"),
)
"""The failures that mean 401, and how each is titled in the problem document.

Mapped one by one rather than through their common base. `dexter.api`'s error map walks the
MRO, so mapping `TokenError` would work — and would also catch every future subclass, including
one that ought to mean something else. Four lines is a small price for a table a reader can
check against the specification.
"""


def use_authentication(builder: ContainerBuilder) -> None:
    """Register the rule registry, the middleware, the caller bindings and the 401 mappings.

    Call once, after `use_api`, and before any `require_authentication`.

    The registry is bound as an instance so `require_authentication` can populate it while
    wiring, before the container is built — the same pattern `dexter.api` uses for its own
    registries.

    `Authentication` and `Principal` are both `Scope.SCOPED`, and the pair is deliberate.
    `Authentication` always resolves and may name nobody, which is what code that merely
    *prefers* a caller should ask for. `Principal` resolves to a caller or raises, so declaring
    one is itself the statement that this code cannot run anonymously — and because a handler's
    dependencies are built inside the request's error handling, that statement renders as a 401
    rather than a 500.

    Being `Scope.SCOPED` is three guarantees rather than a preference: resolving either from
    the root raises `ScopeRequiredError` because the root is not a request, `build()` refuses
    any `Scope.SINGLETON` service that reaches one with `CaptiveDependencyError` so a
    process-wide service cannot capture one caller and serve them to everybody, and within one
    request everything sees the same answer.
    """
    builder.register(AuthenticationRegistry).to_instance(AuthenticationRegistry())
    builder.register(Authentication).to(current_authentication, scope=Scope.SCOPED)
    builder.register(Principal).to(current_principal, scope=Scope.SCOPED)

    register_api_middleware(builder, AuthenticationMiddleware, scope=Scope.SCOPED)

    for error, title in UNAUTHORIZED:
        register_error(builder, error, status=HTTPStatus.UNAUTHORIZED, title=title)


def require_authentication(builder: ContainerBuilder, handler: type[Any], /) -> None:
    """Record that `handler` may only be reached by an authenticated caller.

    Once per handler, whatever its exposures: the rule is about the operation, not the route,
    so a handler reachable two ways is closed both ways by one call.

    Nothing is bound in the container, so there is no `scope=` to choose — a handler is a key in
    a table here, not a dependency. It is registered separately from `register_handler` rather
    than as an argument to it because a handler's exposures and a handler's access rule are
    decided by different people at different times, and threading a keyword through would put
    the security decision in the middle of a routing statement.

    Args:
        builder: The builder `use_authentication` was called on.
        handler: The handler class, exactly as passed to `register_handler`.

    Raises:
        IamNotWiredError: If `use_authentication` has not been called on this builder.
        DuplicateAuthenticationRuleError: If this handler already has a rule.
    """
    _fetch(builder).require(handler)


def _fetch(builder: ContainerBuilder) -> AuthenticationRegistry:
    """Fetch the registry from the builder, or explain that wiring is missing."""
    try:
        return builder.resolve_instance(AuthenticationRegistry)
    except InvalidRegistrationError as error:
        raise IamNotWiredError(
            f"{describe_type(AuthenticationRegistry)} is not registered, so there is "
            f"nothing to register into. Call `use_authentication(builder)` before "
            f"requiring authentication on a handler."
        ) from error
