"""Wiring: how identity services are registered into a container.

The same two shapes every dexter module uses. `use_iam(builder)` registers what the *module*
provides — a clock, a token service, a magic-code service — and takes no configuration.
`register_*(builder, ...)` registers what the *application* contributes, which here is the two
policies, because a signing key is a value an application owns and dexter reads no environment.

    builder = ContainerBuilder()
    use_iam(builder)
    use_in_memory_magic_codes(builder)
    register_token_policy(builder, TokenPolicy(secret=..., issuer="plum"))
    register_magic_code_policy(builder, MagicCodePolicy(secret=...))
    container = builder.build()

Nothing here knows about HTTP. `use_authentication` — the middleware that turns a bearer header
into a `Principal` — lives in `dexter.iam.api`, and a worker application never imports it.

**Where the code store is bound is a separate call, and that is the point.** `use_iam` binding
one would make it unswappable: the container refuses a second binding of the same key, so an
application wanting Redis could not replace what the module had already chosen. Choosing a store
is a topology decision, so it is a `use_*` of its own — the same reason `dexter.notification`
has one per engine.
"""

from dexter.dependency_injection import ContainerBuilder, Scope

from .clock import SystemClock
from .magic_code import MagicCodeService
from .models import Clock, MagicCodePolicy, MagicCodeStore, TokenPolicy
from .stores import InMemoryMagicCodeStore
from .tokens import TokenService


def use_iam(builder: ContainerBuilder) -> None:
    """Register the clock, the token service and the magic-code service.

    Call once, before registering the policies they read.

    All three are `Scope.SINGLETON`. None holds per-request state — a token service holds a
    codec and a policy, both settled before the first request — and rebuilding one per request
    would mean re-deriving an HMAC key on the path of every authenticated call.

    Neither policy is registered here, and the container will refuse to resolve a `TokenService`
    without one. That failure names `TokenPolicy`, which is exactly the call the reader is
    missing, so it is left to say so rather than being pre-empted by a vaguer error here.

    `MagicCodeStore` is not registered here either — see the module docstring.

    **An application's own `Clock` wins.** Bind one before calling this and it is left alone;
    otherwise `SystemClock` is bound. This is the one conditional binding in the module, and it
    is here because the container refuses a second binding of a key — so an unconditional one
    would make the clock the single thing in `dexter.iam` that could never be replaced, which
    is a poor property for the thing that decides when a token expires.
    """
    if not builder.is_registered(Clock):
        builder.register(Clock).to(SystemClock, scope=Scope.SINGLETON)
    builder.register(TokenService).to(TokenService, scope=Scope.SINGLETON)
    builder.register(MagicCodeService).to(MagicCodeService, scope=Scope.SINGLETON)


def use_in_memory_magic_codes(builder: ContainerBuilder) -> None:
    """Bind `MagicCodeStore` to a dictionary in this process.

    The starting topology, and the one the tests use. Codes do not survive a restart and are not
    shared between workers, so a service behind more than one process needs a real store —
    which is one different call here and no other change.

    Both keys are bound, and `MagicCodeStore` resolves *through* the concrete key rather than
    being built a second time from the same class: two bindings of one class produce two
    dictionaries, and then codes are written to one and read from the other.
    """
    builder.register(InMemoryMagicCodeStore).to(
        InMemoryMagicCodeStore, scope=Scope.SINGLETON
    )
    builder.register(MagicCodeStore).to(_the_store, scope=Scope.SINGLETON)


def register_token_policy(builder: ContainerBuilder, policy: TokenPolicy, /) -> None:
    """Bind how tokens are signed and how long they live.

    Nothing is constructed, so there is no `scope=` to choose: an existing object is inherently
    a single object. `scope=` is required on a `register_*` that binds a provider, and this one
    does not — the same rule that leaves it off `dexter.api`'s `register_error`.
    """
    builder.register(TokenPolicy).to_instance(policy)


def register_magic_code_policy(
    builder: ContainerBuilder, policy: MagicCodePolicy, /
) -> None:
    """Bind how codes are generated, how long they live, and how often they may be guessed."""
    builder.register(MagicCodePolicy).to_instance(policy)


def _the_store(store: InMemoryMagicCodeStore) -> InMemoryMagicCodeStore:
    """Resolve the store that is already bound, so both keys name one object."""
    return store
