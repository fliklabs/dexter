"""Turning a domain failure into a response — and knowing when not to.

The rule this file pins is the one most likely to be softened by a well-meaning change: an
exception nobody mapped is **re-raised**, not tidied into a 500 here. A library that converts
each of its consumers' bugs into a neat response body is a library that hides them from every
logger and error tracker they have.
"""

from http import HTTPMethod, HTTPStatus

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from dexter.api import (
    ErrorMap,
    ErrorResponse,
    HttpExposure,
    register_error,
    register_handler,
)
from dexter.api.http.endpoint import PROBLEM_JSON
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
    async def test_an_unmapped_exception_is_re_raised(
        self, booms: ContainerBuilder
    ) -> None:
        async with serving(booms) as client:
            with pytest.raises(RuntimeError, match="nobody mapped"):
                await client.get("/boom")

    async def test_the_frameworks_own_exception_is_left_alone(
        self, booms: ContainerBuilder
    ) -> None:
        async with serving(booms) as client:
            response = await client.get("/boom", params={"kind": "http"})
        assert response.status_code == HTTPStatus.IM_A_TEAPOT
        assert response.json() == {"detail": "a teapot"}

    async def test_a_broad_mapping_does_not_swallow_the_frameworks_signalling(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, Exception, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        async with serving(booms) as client:
            teapot = await client.get("/boom", params={"kind": "http"})
            invalid = await client.get("/boom", params={"count": "not-a-number"})

        assert teapot.status_code == HTTPStatus.IM_A_TEAPOT
        # Still the framework's own 422, in the framework's own shape — not problem+json.
        assert invalid.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
        assert invalid.json()["detail"][0]["loc"] == ["query", "count"]

    async def test_a_broad_mapping_does_catch_an_ordinary_failure(
        self, booms: ContainerBuilder
    ) -> None:
        register_error(booms, Exception, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        async with serving(booms) as client:
            response = await client.get("/boom")
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


class TestBody:
    def test_names_the_failure_and_repeats_the_status(self) -> None:
        body = ErrorResponse.of(
            HTTPStatus.NOT_FOUND, None, RoomUnavailableError("gone")
        )
        assert body.title == "Not Found"
        assert body.status == 404
        assert body.detail == "gone"

    def test_a_title_overrides_the_phrase(self) -> None:
        body = ErrorResponse.of(
            HTTPStatus.NOT_FOUND, "No room", RoomUnavailableError("x")
        )
        assert body.title == "No room"
