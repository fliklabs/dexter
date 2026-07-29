"""Turning a domain failure into a response — and knowing when not to.

The rule this file pins is the one most likely to be softened by a well-meaning change: an
exception nobody mapped is **re-raised**, not tidied into a 500 here. A library that converts
each of its consumers' bugs into a neat response body is a library that hides them from every
logger and error tracker they have.
"""

from http import HTTPMethod, HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from dexter.api import (
    ErrorMap,
    ErrorResponse,
    HttpExposure,
    InvalidField,
    register_error,
    register_handler,
)
from dexter.api.http.problem import PROBLEM_JSON, UNEXPECTED
from dexter.api.models import describe_status
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import NoSuchRoomError, RoomUnavailableError, serving


class Boom(BaseModel):
    kind: str = "unmapped"
    count: int = 0


class BoomHandler:
    """Raise whatever the caller asked for."""

    async def handle(self, request: Boom) -> str:
        if request.kind == "mapped":
            raise RoomUnavailableError("room is taken")
        if request.kind == "derived":
            raise NoSuchRoomError("no such room")
        if request.kind == "http":
            raise HTTPException(status_code=418, detail="a teapot")
        raise RuntimeError("something nobody mapped")


@pytest.fixture
def booms(builder: ContainerBuilder) -> ContainerBuilder:
    """A builder serving the failing handler."""
    register_handler(
        builder,
        BoomHandler,
        HttpExposure(method=HTTPMethod.GET, path="/boom"),
        scope=Scope.TRANSIENT,
    )
    return builder


class TestMapping:
    async def test_a_mapped_exception_becomes_its_status(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "mapped"})
        assert response.status_code == HTTPStatus.CONFLICT

    async def test_the_body_is_problem_json(self, booms: ContainerBuilder) -> None:
        register_error(
            booms, RoomUnavailableError, status=HTTPStatus.CONFLICT, title="Room taken"
        )
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "mapped"})

        assert response.headers["content-type"].startswith(PROBLEM_JSON)
        assert response.json() == {
            "title": "Room taken",
            "status": 409,
            "detail": "room is taken",
        }

    async def test_the_title_defaults_to_the_statuss_own_phrase(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "mapped"})
        assert response.json()["title"] == "Conflict"


class TestHierarchy:
    async def test_registering_a_base_covers_a_subclass(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "derived"})
        assert response.status_code == HTTPStatus.CONFLICT

    async def test_the_most_derived_registration_wins(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        register_error(booms, NoSuchRoomError, status=HTTPStatus.NOT_FOUND)
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "derived"})
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_finds_nothing_when_nothing_covers_it(self) -> None:
        assert ErrorMap().find(RuntimeError("x")) is None

    def test_finds_a_mapping_registered_on_exception_itself(self) -> None:
        errors = ErrorMap()
        errors.register(Exception, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        mapping = errors.find(RuntimeError("x"))
        assert mapping is not None
        assert mapping.status is HTTPStatus.INTERNAL_SERVER_ERROR

    def test_reads_as_the_class_and_the_status(self) -> None:
        errors = ErrorMap()
        errors.register(RoomUnavailableError, status=HTTPStatus.CONFLICT)
        assert "409" in repr(errors.mappings()[0])


class TestWhatIsNotCaught:
    """An unmapped failure is answered, but never silenced.

    Both halves are real at once — the error middleware sends its response and *then*
    re-raises — so a test can only observe one at a time. Which one depends on how the client
    was built, and there is a test here for each.
    """

    async def test_an_unmapped_exception_still_propagates(
        self, booms: ContainerBuilder
    ) -> None:
        """This is what a server logs from. Answering it must not cost the traceback."""
        async with serving(booms) as client:
            with pytest.raises(RuntimeError, match="nobody mapped"):
                await client.get("/boom")

    async def test_an_unmapped_exception_is_answered_as_problem_details(
        self, booms: ContainerBuilder
    ) -> None:
        async with serving(booms, raise_app_exceptions=False) as client:
            response = await client.get("/boom")

        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert response.headers["content-type"].startswith(PROBLEM_JSON)
        assert response.json() == {
            "title": "Internal Server Error",
            "status": 500,
            "detail": UNEXPECTED,
        }

    async def test_an_unmapped_exception_leaks_nothing_about_itself(
        self, booms: ContainerBuilder
    ) -> None:
        """`str()` of an unanticipated failure can carry a connection string."""
        async with serving(booms, raise_app_exceptions=False) as client:
            response = await client.get("/boom")

        assert "nobody mapped" not in response.text

    async def test_the_frameworks_own_exception_keeps_its_status_and_message(
        self, booms: ContainerBuilder
    ) -> None:
        """Rendered uniformly, but a handler that raised one still said what it wanted."""
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "http"})

        assert response.status_code == HTTPStatus.IM_A_TEAPOT
        assert response.headers["content-type"].startswith(PROBLEM_JSON)
        assert response.json() == {
            "title": "I'm a Teapot",
            "status": 418,
            "detail": "a teapot",
        }

    async def test_the_frameworks_own_exception_keeps_a_nonstandard_status(
        self, builder: ContainerBuilder
    ) -> None:
        class Odd(BaseModel):
            pass

        class OddHandler:
            async def handle(self, request: Odd) -> str:
                raise HTTPException(status_code=599, detail="something odd")

        register_handler(
            builder,
            OddHandler,
            HttpExposure(method=HTTPMethod.GET, path="/odd"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/odd")

        assert response.status_code == 599
        assert response.json() == {
            "title": "Error",
            "status": 599,
            "detail": "something odd",
        }

    async def test_the_frameworks_own_exception_keeps_its_headers(
        self, builder: ContainerBuilder
    ) -> None:
        """A 401 without its `WWW-Authenticate` is a broken 401."""

        class Guarded(BaseModel):
            pass

        class GuardedHandler:
            async def handle(self, request: Guarded) -> str:
                raise HTTPException(
                    status_code=401,
                    detail="who are you",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        register_handler(
            builder,
            GuardedHandler,
            HttpExposure(method=HTTPMethod.GET, path="/guarded"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/guarded")

        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_a_broad_mapping_does_not_swallow_the_frameworks_signalling(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, Exception, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        async with serving(booms) as client:
            teapot = await client.get("/boom", params={"kind": "http"})
            invalid = await client.get("/boom", params={"count": "not-a-number"})

        # The status is still the framework's, not the one `Exception` was mapped to.
        assert teapot.status_code == HTTPStatus.IM_A_TEAPOT
        assert invalid.status_code == HTTPStatus.UNPROCESSABLE_CONTENT

    async def test_a_broad_mapping_does_catch_an_ordinary_failure(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, Exception, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        async with serving(booms) as client:
            response = await client.get("/boom")
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


class TestValidationIsRenderedTheSameWay:
    async def test_reports_which_field_was_rejected_and_why(
        self, booms: ContainerBuilder
    ) -> None:
        async with serving(booms) as client:
            response = await client.get("/boom", params={"count": "not-a-number"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
        assert response.headers["content-type"].startswith(PROBLEM_JSON)

        body = response.json()
        assert body["title"] == "Unprocessable Content"
        assert body["status"] == 422
        assert body["errors"][0]["location"] == ["query", "count"]
        assert body["errors"][0]["kind"] == "int_parsing"
        assert body["errors"][0]["message"]

    async def test_carries_no_errors_member_when_nothing_was_rejected(
        self, booms: ContainerBuilder
    ) -> None:
        """An ordinary failure body is three fields, not four with a null."""
        register_error(booms, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "mapped"})

        assert "errors" not in response.json()


class TestConsumerHandlers:
    async def test_a_handler_the_consumer_installed_is_not_replaced(
        self, rooms: ContainerBuilder
    ) -> None:
        """Uniformity is the default, not an imposition."""
        app = FastAPI()

        async def mine(request: Request, exc: Exception) -> Response:
            del request, exc
            return JSONResponse({"mine": True}, status_code=418)

        app.add_exception_handler(StarletteHTTPException, mine)

        async with serving(rooms, app=app) as client:
            response = await client.get("/nothing")

        assert response.json() == {"mine": True}


class TestBody:
    def test_names_the_failure_and_repeats_the_status(self) -> None:
        body = ErrorResponse.of(int(HTTPStatus.NOT_FOUND), None, "gone")
        assert body.title == "Not Found"
        assert body.status == 404
        assert body.detail == "gone"
        assert body.errors is None

    def test_a_title_overrides_the_phrase(self) -> None:
        body = ErrorResponse.of(int(HTTPStatus.NOT_FOUND), "No room", "x")
        assert body.title == "No room"

    def test_keeps_a_status_the_standard_does_not_name(self) -> None:
        """A caller answering 599 meant 599; rounding it would answer a question nobody asked."""
        body = ErrorResponse.of(599, None, "odd")
        assert body.status == 599
        assert body.title == "Error"

    def test_describes_a_standard_status_by_its_phrase(self) -> None:
        assert describe_status(int(HTTPStatus.CONFLICT)) == "Conflict"
        assert describe_status(599) == "Error"

    def test_carries_rejected_fields_when_there_are_any(self) -> None:
        field = InvalidField(location=("body", "nights"), message="too many", kind="le")
        body = ErrorResponse.of(HTTPStatus.UNPROCESSABLE_CONTENT, None, "no", (field,))
        assert body.errors == (field,)
