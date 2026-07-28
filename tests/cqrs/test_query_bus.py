"""The query bus: answers inline, with no ticket."""

import pytest

from dexter.cqrs import (
    BusClosedError,
    QueryBus,
    UnhandledQueryError,
    register_query_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    CountUsers,
    CountUsersHandler,
    GetUser,
    GetUserHandler,
    running,
)


def wire(builder: ContainerBuilder) -> ContainerBuilder:
    register_query_handler(builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT)
    register_query_handler(
        builder, CountUsers, CountUsersHandler, scope=Scope.TRANSIENT
    )
    return builder


class TestHappyPath:
    async def test_returns_the_answer_directly(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(QueryBus)

            assert await bus.ask(GetUser(user_id=42)) == "user-42"

    async def test_asking_twice_answers_twice(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(QueryBus)

            assert await bus.ask(GetUser(user_id=1)) == "user-1"
            assert await bus.ask(GetUser(user_id=2)) == "user-2"


class TestFailures:
    async def test_the_handlers_exception_propagates_to_the_caller(
        self, builder: ContainerBuilder
    ) -> None:
        """A query is answered inline, so its failure arrives inline too."""
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(QueryBus)

            with pytest.raises(RuntimeError, match="counting failed"):
                await bus.ask(CountUsers())

    async def test_an_unhandled_query_raises(self, builder: ContainerBuilder) -> None:
        async with running(builder) as scope:
            bus = await scope.resolve(QueryBus)

            with pytest.raises(UnhandledQueryError, match="register_query_handler"):
                await bus.ask(GetUser(user_id=1))

    async def test_the_unhandled_error_names_the_query(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            bus = await scope.resolve(QueryBus)

            with pytest.raises(UnhandledQueryError) as caught:
                await bus.ask(GetUser(user_id=1))

            assert caught.value.message_type is GetUser


class TestLifecycle:
    async def test_asking_after_close_raises(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(QueryBus)
            await bus.aclose()

            with pytest.raises(BusClosedError, match="query bus is closed"):
                await bus.ask(GetUser(user_id=1))

    async def test_draining_a_query_bus_does_nothing(
        self, builder: ContainerBuilder
    ) -> None:
        """Nothing is outstanding, because a query never leaves the caller's task."""
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(QueryBus)
            await bus.ask(GetUser(user_id=1))

            await bus.drain()
