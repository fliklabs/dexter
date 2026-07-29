"""Building the application, and what it refuses to build."""

import asyncio
from http import HTTPMethod

import pytest
from fastapi import FastAPI

from dexter.api import (
    ApiNotWiredError,
    ExposureRegistry,
    HttpExposure,
    register_handler,
)
from dexter.api.http import create_app
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import GetRoomHandler, SearchHandler


class TestGuards:
    async def test_refuses_a_container_that_was_never_wired(self) -> None:
        container = ContainerBuilder().build()
        try:
            with pytest.raises(ApiNotWiredError, match="use_api"):
                await create_app(container)
        finally:
            await container.aclose()


class TestWhatItBuilds:
    async def test_adds_one_route_per_exposure(self, rooms: ContainerBuilder) -> None:
        container = rooms.build()
        try:
            app = await create_app(container)
        finally:
            await container.aclose()

        paths = {getattr(route, "path", "") for route in app.routes}
        assert {"/rooms/{room_id}", "/bookings", "/rooms"} <= paths

    async def test_builds_an_empty_application_when_nothing_is_registered(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            app = await create_app(container)
        finally:
            await container.aclose()

        assert isinstance(app, FastAPI)

    async def test_names_a_route_after_its_handler(
        self, rooms: ContainerBuilder
    ) -> None:
        container = rooms.build()
        try:
            app = await create_app(container)
        finally:
            await container.aclose()

        names = {getattr(route, "name", None) for route in app.routes}
        assert "get_room" in names

    async def test_an_explicit_name_wins(self, builder: ContainerBuilder) -> None:
        register_handler(
            builder,
            SearchHandler,
            HttpExposure(method=HTTPMethod.GET, path="/rooms", name="search_rooms"),
            scope=Scope.TRANSIENT,
        )
        container = builder.build()
        try:
            app = await create_app(container)
        finally:
            await container.aclose()

        names = {getattr(route, "name", None) for route in app.routes}
        assert "search_rooms" in names

    async def test_routes_are_added_in_registration_order(
        self, builder: ContainerBuilder
    ) -> None:
        """Matching follows insertion, so a literal path must be registered first."""
        register_handler(
            builder,
            SearchHandler,
            HttpExposure(method=HTTPMethod.GET, path="/rooms"),
            scope=Scope.TRANSIENT,
        )
        register_handler(
            builder,
            GetRoomHandler,
            HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}"),
            scope=Scope.TRANSIENT,
        )
        container = builder.build()
        try:
            app = await create_app(container)
        finally:
            await container.aclose()

        ours = [
            path
            for route in app.routes
            if (path := str(getattr(route, "path", ""))).startswith("/rooms")
        ]
        assert ours == ["/rooms", "/rooms/{room_id}"]


class TestItStartsNothing:
    async def test_leaves_no_task_running(self, rooms: ContainerBuilder) -> None:
        container = rooms.build()
        before = len(asyncio.all_tasks())
        try:
            await create_app(container)
        finally:
            await container.aclose()

        assert len(asyncio.all_tasks()) == before

    async def test_does_not_close_the_container_it_was_given(
        self, rooms: ContainerBuilder
    ) -> None:
        """The application does not own the container; the consumer does."""
        container = rooms.build()
        try:
            await create_app(container)
            # Still usable, because nothing closed it.
            assert await container.resolve(ExposureRegistry) is not None
        finally:
            await container.aclose()
