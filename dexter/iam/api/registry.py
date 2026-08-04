"""Which handlers need a caller.

One registry, populated while wiring and read-only in practice thereafter: `use_authentication`
binds it as an instance, `require_authentication` fills it, and the container is built
afterwards. The same shape as `dexter.api`'s own two registries, for the same reason — a
decision settled at startup should not be re-derived per request.

It holds handler **classes** as keys, because that is what an `Invocation` carries and what is
available before anything is constructed. A rule keyed on a route would have to be repeated for
every exposure of the same handler, and would then be able to disagree with itself.

**Default open.** A handler nobody named is `ANONYMOUS`. The alternative — everything closed
until opened — is the safer default in the abstract and the wrong one here: it makes the login
routes, the health check and every public page carry a line of wiring, and it turns adding a
public route into a puzzle. What makes default-open safe is that the decision is *visible*:
`requirements()` lists every rule, and an application that wants the other default can assert
over `ExposureRegistry.records()` that every handler is named.
"""

from enum import StrEnum
from typing import Any

from dexter.commons import describe_type

from ..errors import DuplicateAuthenticationRuleError


class AuthenticationRequirement(StrEnum):
    """Whether a handler needs a caller."""

    ANONYMOUS = "ANONYMOUS"
    """No token is needed. One that is presented anyway is still verified."""

    REQUIRED = "REQUIRED"
    """A valid access token is needed, and the request is refused without one."""


def describe_requirement(requirement: AuthenticationRequirement, /) -> str:
    """Render a requirement as the symbol a caller would type.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence. In a message aimed at
    a developer the qualified symbol is more useful: it is what they have to write.
    """
    return f"AuthenticationRequirement.{requirement.name}"


class AuthenticationRegistry:
    """Every handler that has been given an authentication rule."""

    __slots__ = ("_rules",)

    def __init__(self) -> None:
        """Start with nothing required."""
        self._rules: dict[type[Any], AuthenticationRequirement] = {}

    def require(self, handler: type[Any], /) -> None:
        """Record that `handler` may only be reached by an authenticated caller.

        Raises `DuplicateAuthenticationRuleError` on a second call for the same handler: either
        it repeats the first, or two pieces of wiring disagree and which one wins would depend
        on the order they ran in.
        """
        if handler in self._rules:
            raise DuplicateAuthenticationRuleError(
                f"{describe_type(handler)} already has an authentication rule; "
                f"one handler has one."
            )
        self._rules[handler] = AuthenticationRequirement.REQUIRED

    def requirement_for(self, handler: type[Any], /) -> AuthenticationRequirement:
        """What `handler` needs. `ANONYMOUS` for a handler nobody named."""
        return self._rules.get(handler, AuthenticationRequirement.ANONYMOUS)

    def requires(self, handler: type[Any], /) -> bool:
        """Whether `handler` needs an authenticated caller."""
        return self.requirement_for(handler) is AuthenticationRequirement.REQUIRED

    def requirements(self) -> tuple[tuple[type[Any], AuthenticationRequirement], ...]:
        """Every rule, in registration order.

        This is what makes default-open auditable: a test can read it back and compare it
        against the exposure registry, and fail when a new route was added without a decision.
        """
        return tuple(self._rules.items())

    def __repr__(self) -> str:
        return f"AuthenticationRegistry(required={len(self._rules)})"
