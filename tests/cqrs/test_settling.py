"""Leaving a scope settles its buses, without anyone calling `drain`.

The container owns this: `use_cqrs` binds `BusGroup` with `dispose=BusGroup.settle`, so
teardown is the container's job rather than a discipline the application has to remember.
"""

import pytest

from dexter.cqrs import (
    BusGroup,
    CommandBus,
    EventBus,
    register_command_handler,
    register_event_handler,
)
from dexter.dependency_injection import ContainerBuilder, DisposalError, Scope

from .conftest import (
    Cascade,
    CascadeHandler,
    Chain,
    ChainHandler,
    CreateUser,
    CreateUserHandler,
    Explode,
    ExplodeHandler,
    Ledger,
    RecordFirst,
    UserCreated,
)


class TestLeavingAScope:
    async def test_waits_for_a_dispatch_nobody_redeemed(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                bus.dispatch(CreateUser(email="a@b.c"))

            assert ledger.entries == ["created a@b.c"]
        finally:
            await container.aclose()

    async def test_waits_for_an_event_published_by_a_command_handler(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        """The regression test for settling buses in creation order.

        The event bus is constructed *inside* the command handler, so it finishes after the
        command bus. Draining in reverse creation order would drain it first, while it is
        still empty, and the reaction would escape the scope entirely.
        """
        register_command_handler(
            builder, Cascade, CascadeHandler, scope=Scope.TRANSIENT
        )
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                bus.dispatch(Cascade())

            assert ledger.entries == ["command ran", "first saw 1"]
        finally:
            await container.aclose()

    async def test_waits_for_work_a_handler_started_on_the_same_bus(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        """`drain` loops: waiting on one snapshot would miss what that work dispatched."""
        register_command_handler(builder, Chain, ChainHandler, scope=Scope.TRANSIENT)
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                bus.dispatch(Chain())

            assert ledger.entries == ["outer ran", "created chained@x.y"]
        finally:
            await container.aclose()

    async def test_reports_a_failure_nobody_redeemed(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()

        async def leave_a_scope_with_a_failure_in_flight() -> None:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                bus.dispatch(Explode(reason="unwatched"))

        try:
            with pytest.raises(DisposalError) as caught:
                await leave_a_scope_with_a_failure_in_flight()

            assert len(caught.value.exceptions) == 1
        finally:
            await container.aclose()

    async def test_the_original_failure_is_reachable_with_except_star(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        caught: list[str] = []
        try:
            try:
                async with container.scope() as scope:
                    bus = await scope.resolve(CommandBus)
                    bus.dispatch(Explode(reason="reachable"))
            except* RuntimeError as group:
                caught.append(type(group).__name__)

            assert caught, "except* RuntimeError did not reach the handler's failure"
        finally:
            await container.aclose()

    async def test_says_nothing_about_a_failure_already_redeemed(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                ticket = bus.dispatch(Explode())
                with pytest.raises(RuntimeError):
                    await ticket.result()
        finally:
            await container.aclose()

    async def test_the_buses_are_closed_afterwards(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)

            assert bus.pending == 0
        finally:
            await container.aclose()


class TestBusGroup:
    async def test_counts_work_across_every_bus_in_the_scope(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, Cascade, CascadeHandler, scope=Scope.TRANSIENT
        )
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        container = builder.build()
        try:
            async with container.scope() as scope:
                group = await scope.resolve(BusGroup)
                commands = await scope.resolve(CommandBus)
                await scope.resolve(EventBus)

                assert group.pending == 0
                commands.dispatch(Cascade())
                assert group.pending == 1
        finally:
            await container.aclose()

    async def test_every_bus_in_a_scope_shares_one_group(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            async with container.scope() as first:
                group = await first.resolve(BusGroup)
                await first.resolve(CommandBus)
                await first.resolve(EventBus)

            async with container.scope() as second:
                other = await second.resolve(BusGroup)

            assert group is not other
        finally:
            await container.aclose()
