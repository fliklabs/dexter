"""Fixtures and a sample domain local to the IAM tests.

Two things carry most of the weight here. `FrozenClock` is what makes expiry testable: every
decision in `dexter.iam` reads a `Clock`, so a test moves time by assignment instead of by
sleeping, and a suite that checks a fifteen-minute lifetime still runs in milliseconds.

The rest is a tiny API — one open handler, one closed one, and one that asks for a `Principal`
— driven in process over ASGI. There is no socket, no port and no server: the tests call the
application object the same way a server would, on the test's own event loop. That last part
matters, because the framework's own test client is synchronous and would run the app on a
separate thread, where the per-task request context does not reach.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPMethod
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from dexter.api import HttpExposure, register_handler, use_api
from dexter.api.http import create_app
from dexter.dependency_injection import Container, ContainerBuilder, Scope
from dexter.iam import (
    Clock,
    InMemoryMagicCodeStore,
    MagicCodePolicy,
    MagicCodeService,
    Principal,
    TokenPolicy,
    TokenService,
    register_magic_code_policy,
    register_token_policy,
    use_iam,
    use_in_memory_magic_codes,
)
from dexter.iam.api import Authentication, require_authentication, use_authentication

SECRET = "a-signing-key-long-enough-to-be-worth-something"
"""Thirty-two characters or more, which `TokenPolicy` insists on."""

ISSUER = "tests"

EPOCH = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
"""A fixed instant every test starts from, so nothing depends on when the suite ran."""


class FrozenClock:
    """A `Clock` that only moves when a test moves it."""

    __slots__ = ("instant",)

    def __init__(self, instant: datetime = EPOCH) -> None:
        """Start at `instant`."""
        self.instant = instant

    def now(self) -> datetime:
        """The instant this clock is currently at."""
        return self.instant

    def advance(self, delta: timedelta) -> None:
        """Move forward by `delta`."""
        self.instant += delta


def make_token_policy(**overrides: Any) -> TokenPolicy:
    """A policy every field of which a test may replace."""
    fields: dict[str, Any] = {"secret": SECRET, "issuer": ISSUER}
    fields.update(overrides)
    return TokenPolicy(**fields)


def make_code_policy(**overrides: Any) -> MagicCodePolicy:
    """A magic-code policy every field of which a test may replace."""
    fields: dict[str, Any] = {"secret": SECRET}
    fields.update(overrides)
    return MagicCodePolicy(**fields)


def make_tokens(clock: Clock | None = None, **overrides: Any) -> TokenService:
    """A token service on a frozen clock unless the test supplies its own."""
    return TokenService(make_token_policy(**overrides), clock or FrozenClock())


def make_codes(clock: Clock | None = None, **overrides: Any) -> MagicCodeService:
    """A magic-code service over a fresh in-memory store."""
    return MagicCodeService(
        make_code_policy(**overrides), InMemoryMagicCodeStore(), clock or FrozenClock()
    )


# ── the sample API ───────────────────────────────────────────────────


class Empty(BaseModel):
    """A request carrying nothing, for routes that take no input."""


class View(BaseModel):
    """What the sample handlers answer with."""

    subject: str


class OpenApi:
    """A route anyone may reach."""

    def __init__(self, authentication: Authentication) -> None:
        self.authentication = authentication

    async def handle(self, request: Empty) -> View:
        principal = self.authentication.principal
        return View(subject=principal.subject if principal else "anonymous")


class ClosedApi:
    """A route only an authenticated caller may reach."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    async def handle(self, request: Empty) -> View:
        return View(subject=self.principal.subject)


class DemandingApi:
    """An open route whose handler asks for a caller anyway."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    async def handle(self, request: Empty) -> View:
        return View(subject=self.principal.subject)


class Expensive:
    """Records every time it is built, so a test can prove a refusal built nothing."""

    def __init__(self) -> None:
        BUILT.append(self)


BUILT: list[Expensive] = []
"""Every `Expensive` ever constructed. Cleared by the `built` fixture."""


class CostlyApi:
    """A closed route with an expensive dependency."""

    def __init__(self, principal: Principal, expensive: Expensive) -> None:
        self.principal = principal
        self.expensive = expensive

    async def handle(self, request: Empty) -> View:
        return View(subject=self.principal.subject)


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def clock() -> FrozenClock:
    """A clock stopped at `EPOCH`."""
    return FrozenClock()


@pytest.fixture
def built() -> list[Expensive]:
    """The record of expensive constructions, empty at the start of each test."""
    BUILT.clear()
    return BUILT


@pytest.fixture
def bare_builder() -> ContainerBuilder:
    """A builder with nothing wired, for asserting what happens without `use_authentication`."""
    return ContainerBuilder()


@pytest.fixture
def builder(clock: FrozenClock) -> ContainerBuilder:
    """A builder with the API and IAM wired, on a clock the test controls."""
    container_builder = ContainerBuilder()

    # Bound *before* `use_iam`, which leaves an application's own clock alone. That is what
    # lets a test expire a token by moving `clock` rather than by waiting fifteen minutes.
    container_builder.register(Clock).to_instance(clock)

    use_api(container_builder)
    use_iam(container_builder)
    use_in_memory_magic_codes(container_builder)
    register_token_policy(container_builder, make_token_policy())
    register_magic_code_policy(container_builder, make_code_policy())
    use_authentication(container_builder)
    return container_builder


@pytest.fixture
def routes(builder: ContainerBuilder) -> ContainerBuilder:
    """The sample API, registered on `builder`."""
    builder.register(Expensive).to(Expensive, scope=Scope.TRANSIENT)

    register_handler(
        builder,
        OpenApi,
        HttpExposure(method=HTTPMethod.GET, path="/open"),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        ClosedApi,
        HttpExposure(method=HTTPMethod.GET, path="/closed"),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        DemandingApi,
        HttpExposure(method=HTTPMethod.GET, path="/demanding"),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        CostlyApi,
        HttpExposure(method=HTTPMethod.GET, path="/costly"),
        scope=Scope.TRANSIENT,
    )
    require_authentication(builder, ClosedApi)
    require_authentication(builder, CostlyApi)
    return builder


@asynccontextmanager
async def serving(
    builder: ContainerBuilder, *, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    """Build the container, mount the app, and yield a client speaking to it in process."""
    container = builder.build()
    try:
        app = await create_app(container)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, raise_app_exceptions=raise_app_exceptions
            ),
            base_url="http://api",
        ) as client:
            yield client
    finally:
        await container.aclose()


@asynccontextmanager
async def running(builder: ContainerBuilder) -> AsyncIterator[Container]:
    """Build the container and yield one scope, closing both afterwards."""
    container = builder.build()
    try:
        async with container.scope() as scope:
            yield scope
    finally:
        await container.aclose()


def bearer(token: str) -> dict[str, str]:
    """The header a client sends a token in."""
    return {"Authorization": f"Bearer {token}"}
