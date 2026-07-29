"""Middleware: order, where instances come from, and what refusing a request looks like."""

from http import HTTPMethod, HTTPStatus
from typing import Any

from pydantic import BaseModel

from dexter.api import (
    ApiNext,
    HttpExposure,
    Invocation,
    RequestContext,
    register_api_middleware,
    register_error,
    register_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    GetRoomHandler,
    Inner,
    Ledger,
    Outer,
    RoomUnavailableError,
    ShortCircuit,
    Tenant,
    serving,
)


class TestOrder:
    async def test_the_first_registered_is_outermost(
        self, rooms: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_api_middleware(rooms, Outer, scope=Scope.SCOPED)
        register_api_middleware(rooms, Inner, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            await client.get("/rooms/1")

        assert ledger.entries == [
            "outer in",
            "inner in",
            "get 1",
            "inner out",
            "outer out",
        ]

    async def test_an_empty_pipeline_runs_the_handler_directly(
        self, rooms: ContainerBuilder, ledger: Ledger
    ) -> None:
        async with serving(rooms) as client:
            await client.get("/rooms/1")
        assert ledger.entries == ["get 1"]


class TestResolution:
    async def test_middleware_and_handler_share_the_request_scope(
        self, rooms: ContainerBuilder
    ) -> None:
        seen: list[RequestContext] = []

        class Watcher:
            def __init__(self, context: RequestContext) -> None:
                seen.append(context)

            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                seen.append(invocation.context)
                return await call_next(invocation)

        register_api_middleware(rooms, Watcher, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            await client.get("/rooms/1")

        assert len(seen) == 2
        assert seen[0] is seen[1]

    async def test_a_scoped_middleware_is_rebuilt_for_each_request(
        self, rooms: ContainerBuilder
    ) -> None:
        built: list[object] = []

        class Counted:
            def __init__(self) -> None:
                built.append(self)

            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                return await call_next(invocation)

        register_api_middleware(rooms, Counted, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            await client.get("/rooms/1")
            await client.get("/rooms/2")

        assert len(built) == 2
        assert built[0] is not built[1]


class TestShortCircuiting:
    async def test_refusing_never_reaches_the_handler(
        self, rooms: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_api_middleware(rooms, ShortCircuit, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            response = await client.get("/rooms/1")

        assert ledger.entries == ["refused"]
        assert response.json()["tenant"] == "refused"

    async def test_refusing_never_even_builds_the_handler(
        self, rooms: ContainerBuilder
    ) -> None:
        """A request turned away must not construct the handler's dependency graph."""
        built: list[object] = []

        class Expensive:
            def __init__(self) -> None:
                built.append(self)

        class Costly:
            def __init__(self, expensive: Expensive) -> None:
                self.expensive = expensive

            async def handle(self, request: BaseModel) -> str:
                return "never"

        class Refuse:
            # Whatever a middleware short-circuits with is still serialised through the
            # handler's declared response model, so it has to satisfy it.
            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                return "refused"

        rooms.register(Expensive).to(Expensive, scope=Scope.SCOPED)
        register_handler(
            rooms,
            Costly,
            HttpExposure(method=HTTPMethod.GET, path="/costly"),
            scope=Scope.TRANSIENT,
        )
        register_api_middleware(rooms, Refuse, scope=Scope.SCOPED)

        async with serving(rooms) as client:
            response = await client.get("/costly")

        assert response.json() == "refused"
        assert built == []

    async def test_the_handler_class_is_visible_before_it_is_built(
        self, rooms: ContainerBuilder
    ) -> None:
        seen: list[type[object]] = []

        class Peek:
            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                seen.append(invocation.handler)
                return await call_next(invocation)

        register_api_middleware(rooms, Peek, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            await client.get("/rooms/1")

        assert seen == [GetRoomHandler]

    async def test_what_it_returns_must_satisfy_the_route_it_refused(
        self, rooms: ContainerBuilder
    ) -> None:
        """The trap in refusing by returning: the response model is still the handler's.

        A middleware guarding one handler can return that handler's response. One guarding
        several cannot, because each declares something different — and whatever it returns is
        serialised through whichever route it happened to refuse.
        """
        register_api_middleware(rooms, ShortCircuit, scope=Scope.SCOPED)
        async with serving(rooms, raise_app_exceptions=False) as client:
            fits = await client.get("/rooms/1")  # declares RoomView
            clashes = await client.get("/rooms")  # declares list[str]

        assert fits.status_code == HTTPStatus.OK
        assert clashes.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

    async def test_raising_refuses_every_route_whatever_it_returns(
        self, rooms: ContainerBuilder
    ) -> None:
        """Which is why a middleware that spans handlers should refuse by raising."""

        class Forbid:
            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                raise RoomUnavailableError("not for you")

        register_error(rooms, RoomUnavailableError, status=HTTPStatus.FORBIDDEN)
        register_api_middleware(rooms, Forbid, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            view = await client.get("/rooms/1")  # declares RoomView
            listing = await client.get("/rooms")  # declares list[str]

        assert view.status_code == HTTPStatus.FORBIDDEN
        assert listing.status_code == HTTPStatus.FORBIDDEN
        assert listing.json()["detail"] == "not for you"

    async def test_a_refusal_by_raising_carries_its_message(
        self, rooms: ContainerBuilder
    ) -> None:
        class Forbid:
            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                raise RoomUnavailableError("not for you")

        register_error(rooms, RoomUnavailableError, status=HTTPStatus.FORBIDDEN)
        register_api_middleware(rooms, Forbid, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            response = await client.get("/rooms/1")

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response.json()["detail"] == "not for you"


class TestPassingSomethingInward:
    async def test_what_middleware_writes_a_handler_reads(
        self, builder: ContainerBuilder
    ) -> None:
        class Whoami(BaseModel):
            pass

        class WhoamiHandler:
            def __init__(self, context: RequestContext) -> None:
                self.context = context

            async def handle(self, request: Whoami) -> str:
                return str(self.context.state.get("tenant"))

        register_api_middleware(builder, Tenant, scope=Scope.SCOPED)
        register_handler(
            builder,
            WhoamiHandler,
            HttpExposure(method=HTTPMethod.GET, path="/whoami"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/whoami", headers={"X-Tenant": "acme"})

        assert response.json() == "acme"

    async def test_a_typed_binding_reaches_a_service_that_never_mentions_http(
        self, builder: ContainerBuilder
    ) -> None:
        """The replacement for smuggling a caller through a global."""

        class Tenancy:
            def __init__(self, name: str) -> None:
                self.name = name

        class Rooms:
            def __init__(self, tenancy: Tenancy) -> None:
                self.tenancy = tenancy

        def current_tenancy(context: RequestContext) -> Tenancy:
            return Tenancy(context.headers.get("x-tenant") or "anonymous")

        class Listing(BaseModel):
            pass

        class ListingHandler:
            def __init__(self, rooms: Rooms) -> None:
                self.rooms = rooms

            async def handle(self, request: Listing) -> str:
                return self.rooms.tenancy.name

        builder.register(Tenancy).to(current_tenancy, scope=Scope.SCOPED)
        builder.register(Rooms).to(Rooms, scope=Scope.SCOPED)
        register_handler(
            builder,
            ListingHandler,
            HttpExposure(method=HTTPMethod.GET, path="/listing"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            named = await client.get("/listing", headers={"X-Tenant": "acme"})
            anonymous = await client.get("/listing")

        assert named.json() == "acme"
        assert anonymous.json() == "anonymous"


class TestInvocation:
    async def test_carries_the_request_the_handler_and_the_exposure(
        self, rooms: ContainerBuilder
    ) -> None:
        seen: list[Invocation] = []

        class Capture:
            async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
                seen.append(invocation)
                return await call_next(invocation)

        register_api_middleware(rooms, Capture, scope=Scope.SCOPED)
        async with serving(rooms) as client:
            await client.get("/rooms/7")

        invocation = seen[0]
        assert invocation.request.room_id == 7
        assert invocation.handler is GetRoomHandler
        assert isinstance(invocation.exposure, HttpExposure)
        assert "GetRoomHandler" in repr(invocation)
