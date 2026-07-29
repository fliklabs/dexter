"""Fixtures and a sample domain local to the API tests.

The domain is deliberately tiny: one handler reading a path parameter and a query flag, one
taking a body, one that fails, and a couple of middleware. Everything records into a `Ledger`
so tests can assert on what actually ran and in what order, rather than on internal state.

The app is driven in-process over ASGI. There is no socket, no port and no server — the tests
call the application object the same way a server would, on the test's own event loop. That
last part matters: the framework's own test client is synchronous and runs the app on a
separate portal thread, which would make the per-task isolation this module depends on
untestable.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPMethod, HTTPStatus
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from dexter.api import (
    ApiNext,
    HttpExposure,
    Invocation,
    RequestContext,
    register_handler,
    use_api,
)
from dexter.api.http import create_app
from dexter.dependency_injection import Container, ContainerBuilder, Scope


class Ledger:
    """Records what handlers, middleware and disposal did, in order."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, entry: str) -> None:
        self.entries.append(entry)


class Gate:
    """Lets a test hold a handler open and release it on demand."""

    def __init__(self) -> None:
        self.opened = asyncio.Event()
        self.arrived = asyncio.Event()

    def release(self) -> None:
        self.opened.set()

    async def wait(self) -> None:
        self.arrived.set()
        await self.opened.wait()


class RoomUnavailableError(Exception):
    """A domain failure, for the tests that map one to a status."""


class NoSuchRoomError(RoomUnavailableError):
    """A more derived domain failure, for the tests that walk the hierarchy."""


# ── the sample domain ────────────────────────────────────────────────


class GetRoom(BaseModel):
    room_id: int
    verbose: bool = False


class RoomView(BaseModel):
    room_id: int
    verbose: bool
    tenant: str = ""


class BookRoom(BaseModel):
    room_id: int
    nights: int


class Booking(BaseModel):
    reference: str


class Search(BaseModel):
    town: str = ""
    limit: int = 10


class GetRoomHandler:
    """Describe one room."""

    def __init__(self, context: RequestContext, ledger: Ledger) -> None:
        self.context = context
        self.ledger = ledger

    async def handle(self, request: GetRoom) -> RoomView:
        self.ledger.record(f"get {request.room_id}")
        if request.room_id == 404:
            raise NoSuchRoomError(f"no room {request.room_id}")
        return RoomView(
            room_id=request.room_id,
            verbose=request.verbose,
            tenant=self.context.headers.get("x-tenant", ""),
        )


class BookRoomHandler:
    """Book a room."""

    def __init__(self, context: RequestContext) -> None:
        self.context = context

    async def handle(self, request: BookRoom) -> Booking:
        self.context.set_status(HTTPStatus.CREATED)
        self.context.set_header("location", f"/rooms/{request.room_id}")
        return Booking(reference=f"BK-{request.room_id}-{request.nights}")


class SearchHandler:
    """Search for rooms."""

    async def handle(self, request: Search) -> list[str]:
        return [f"{request.town}-{index}" for index in range(request.limit)]


# ── middleware ───────────────────────────────────────────────────────


class Outer:
    """Records that it wrapped everything else."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        self.ledger.record("outer in")
        result = await call_next(invocation)
        self.ledger.record("outer out")
        return result


class Inner:
    """Records that it ran inside `Outer`."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        self.ledger.record("inner in")
        result = await call_next(invocation)
        self.ledger.record("inner out")
        return result


class Tenant:
    """Reads a header into the context's scratch space, for a handler to find."""

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        invocation.context.state["tenant"] = invocation.context.headers.get("x-tenant")
        return await call_next(invocation)


class ShortCircuit:
    """Refuses the request without calling the handler."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        self.ledger.record("refused")
        return RoomView(room_id=0, verbose=False, tenant="refused")


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def ledger() -> Ledger:
    """A fresh ledger for one test."""
    return Ledger()


@pytest.fixture
def gate() -> Gate:
    """A fresh gate for one test."""
    return Gate()


@pytest.fixture
def builder(ledger: Ledger, gate: Gate) -> ContainerBuilder:
    """A builder with the API module wired and the test collaborators bound."""
    container_builder = ContainerBuilder()
    container_builder.register(Ledger).to_instance(ledger)
    container_builder.register(Gate).to_instance(gate)
    use_api(container_builder)
    return container_builder


@pytest.fixture
def bare_builder() -> ContainerBuilder:
    """A builder with nothing wired, for asserting what happens without `use_api`."""
    return ContainerBuilder()


@pytest.fixture
def rooms(builder: ContainerBuilder) -> ContainerBuilder:
    """The sample domain, registered on `builder`."""
    register_handler(
        builder,
        GetRoomHandler,
        HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}", tags=("rooms",)),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        BookRoomHandler,
        HttpExposure(
            method=HTTPMethod.POST, path="/bookings", status=HTTPStatus.CREATED
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        SearchHandler,
        HttpExposure(method=HTTPMethod.GET, path="/rooms"),
        scope=Scope.TRANSIENT,
    )
    return builder


@asynccontextmanager
async def serving(
    builder: ContainerBuilder,
    *,
    prefix: str = "",
    app: FastAPI | None = None,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    """Build the container, mount the app, and yield a client speaking to it in process.

    `raise_app_exceptions=False` makes the client return the 500 an unhandled failure produced
    instead of re-raising the failure itself. Both halves are real and a test can only observe
    one at a time: the error middleware sends its response *and then* re-raises, which is what
    lets a server log what went wrong. The default keeps the exception, because a test that
    quietly swallowed one would hide exactly the bug the suite exists to catch.
    """
    container = builder.build()
    try:
        built = await create_app(container, prefix=prefix, app=app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=built, raise_app_exceptions=raise_app_exceptions
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


def make_context(**overrides: Any) -> RequestContext:
    """A `RequestContext` for unit-testing something that needs one, with no transport."""
    fields: dict[str, Any] = {"handler": GetRoomHandler, "method": "GET", "path": "/x"}
    fields.update(overrides)
    return RequestContext(**fields)
