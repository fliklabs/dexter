"""Putting authentication in front of registered handlers.

The only part of `dexter.iam` that knows an API exists. Importing this package is what pulls
`dexter.api` in, which is why it is a package of its own rather than a few files beside the
rest: the boundary is a directory a test can walk, and a worker application minting tokens for
a queue imports none of it.

    use_api(builder)
    use_iam(builder)
    use_authentication(builder)
    require_authentication(builder, PickupApi)

After that a handler asks for whoever is calling the same way it asks for anything else::

    class WhoAmI:
        def __init__(self, principal: Principal) -> None:
            self.principal = principal

        async def handle(self, request: Empty) -> Identity:
            return Identity(subject=self.principal.subject)

Declaring a `Principal` says the operation needs a caller and answers 401 without one.
Declaring an `Authentication` says it works either way and asks `is_authenticated`.
"""

from .middleware import HEADER as HEADER
from .middleware import SCHEME as SCHEME
from .middleware import STATE_KEY as STATE_KEY
from .middleware import Authentication as Authentication
from .middleware import AuthenticationMiddleware as AuthenticationMiddleware
from .middleware import current_authentication as current_authentication
from .middleware import current_principal as current_principal
from .registry import AuthenticationRegistry as AuthenticationRegistry
from .registry import AuthenticationRequirement as AuthenticationRequirement
from .registry import describe_requirement as describe_requirement
from .use import UNAUTHORIZED as UNAUTHORIZED
from .use import require_authentication as require_authentication
from .use import use_authentication as use_authentication
