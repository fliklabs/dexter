"""Middleware: order, short-circuiting, and reaching every bus."""

import pytest

from dexter.cqrs import (
    CommandBus,
    DuplicateMiddlewareError,
    EventBus,
    QueryBus,
    register_command_handler,
    register_event_handler,
    register_middleware,
    register_query_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    CreateUser,
    CreateUserHandler,
    Explode,
    ExplodeHandler,
    GetUser,
    GetUserHandler,
    Inner,
    Ledger,
    Outer,
    RecordFirst,
    ShortCircuit,
    UserCreated,
    running,
)


def wire(builder: ContainerBuilder) -> ContainerBuilder:
    register_command_handler(
        builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
    )
    register_query_handler(builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT)
    register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
    return builder


class TestOrdering:
    async def test_the_first_registered_is_the_outermost(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_middleware(builder, Outer, scope=Scope.SCOPED)
        register_middleware(builder, Inner, scope=Scope.SCOPED)
        wire(builder)

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.dispatch(CreateUser(email="a@b.c")).result()

        assert ledger.entries == [
            "outer in",
            "inner in",
            "created a@b.c",
            "inner out",
            "outer out",
        ]

    async def test_an_empty_pipeline_reaches_the_handler(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        wire(builder)

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.dispatch(CreateUser(email="a@b.c")).result()

        assert ledger.entries == ["created a@b.c"]


class TestEveryBus:
    async def test_middleware_wraps_a_query(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_middleware(builder, Outer, scope=Scope.SCOPED)
        wire(builder)

        async with running(builder) as scope:
            bus = await scope.resolve(QueryBus)

            assert await bus.ask(GetUser(user_id=1)) == "user-1"

        assert ledger.entries == ["outer in", "outer out"]

    async def test_middleware_wraps_a_publish_once_not_once_per_handler(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        """One dispatch is one pass through the pipeline, the same as a command."""
        register_middleware(builder, Outer, scope=Scope.SCOPED)
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            await bus.publish(UserCreated(user_id=2)).result()

        assert ledger.entries == ["outer in", "first saw 2", "outer out"]


class TestShortCircuit:
    async def test_not_calling_next_stops_the_handler_running(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_middleware(builder, ShortCircuit, scope=Scope.SCOPED)
        wire(builder)

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)

            assert await bus.dispatch(CreateUser(email="a@b.c")).result() == -1

        assert ledger.entries == ["short circuited"]

    async def test_middleware_after_a_short_circuit_never_runs(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_middleware(builder, ShortCircuit, scope=Scope.SCOPED)
        register_middleware(builder, Inner, scope=Scope.SCOPED)
        wire(builder)

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.dispatch(CreateUser(email="a@b.c")).result()

        assert ledger.entries == ["short circuited"]


class TestFailures:
    async def test_a_handler_failure_propagates_through_the_pipeline(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_middleware(builder, Outer, scope=Scope.SCOPED)
        register_command_handler(
            builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Explode(reason="through the pipeline"))

            with pytest.raises(RuntimeError, match="through the pipeline"):
                await ticket.result()

            await bus.drain()

        assert ledger.entries == ["outer in"], (
            "the outer half should not have completed"
        )


class TestRegistration:
    def test_the_same_middleware_cannot_be_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        register_middleware(builder, Outer, scope=Scope.SCOPED)

        with pytest.raises(DuplicateMiddlewareError, match="would run twice"):
            register_middleware(builder, Outer, scope=Scope.SCOPED)
