"""Tickets and draining: who hears about a failure, and exactly once."""

import asyncio

import pytest

from dexter.cqrs import (
    CommandBus,
    DispatchFailedError,
    EventBus,
    register_command_handler,
    register_event_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    Block,
    BlockHandler,
    CreateUser,
    CreateUserHandler,
    Explode,
    ExplodeHandler,
    FailWithValueError,
    Gate,
    Ledger,
    UserCreated,
    running,
)


def wire(builder: ContainerBuilder) -> ContainerBuilder:
    register_command_handler(
        builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
    )
    register_command_handler(builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT)
    register_command_handler(builder, Block, BlockHandler, scope=Scope.TRANSIENT)
    return builder


class TestCancellation:
    async def test_cancelling_one_waiter_does_not_cancel_the_work(
        self, builder: ContainerBuilder, ledger: Ledger, gate: Gate
    ) -> None:
        """`result()` is shielded, so an impatient caller cannot destroy a shared outcome."""
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Block())

            waiter = asyncio.create_task(ticket.result())
            await gate.arrived.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

            gate.release()
            assert await ticket.result() == 99

        assert ledger.entries == ["unblocked"]

    async def test_a_second_holder_still_gets_the_result(
        self, builder: ContainerBuilder, gate: Gate
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Block())

            first = asyncio.create_task(ticket.result())
            second = asyncio.create_task(ticket.result())
            await gate.arrived.wait()
            first.cancel()
            gate.release()

            assert await second == 99


class TestDraining:
    async def test_drain_reports_a_failure_nobody_redeemed(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            bus.dispatch(Explode(reason="unwatched"))

            with pytest.raises(DispatchFailedError) as caught:
                await bus.drain()

            assert len(caught.value.exceptions) == 1
            assert isinstance(caught.value.exceptions[0], RuntimeError)

    async def test_drain_stays_quiet_about_a_failure_already_redeemed(
        self, builder: ContainerBuilder
    ) -> None:
        """Raising it twice would produce a second exception at scope exit."""
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Explode())

            with pytest.raises(RuntimeError):
                await ticket.result()

            await bus.drain()

    async def test_drain_waits_for_work_still_running(
        self, builder: ContainerBuilder, ledger: Ledger, gate: Gate
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            bus.dispatch(Block())
            await gate.arrived.wait()
            gate.release()

            await bus.drain()

        assert ledger.entries == ["unblocked"]

    async def test_draining_twice_is_harmless(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.dispatch(CreateUser(email="a@b.c")).result()

            await bus.drain()
            await bus.drain()

    async def test_drain_reports_every_unredeemed_failure_together(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            bus.dispatch(Explode(reason="one"))
            bus.dispatch(Explode(reason="two"))

            with pytest.raises(DispatchFailedError) as caught:
                await bus.drain()

            assert len(caught.value.exceptions) == 2
            assert "2 of 2 command dispatches" in caught.value.args[0]

    async def test_drain_on_the_event_bus_reports_unredeemed_handler_failures(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            bus.publish(UserCreated(user_id=1))

            with pytest.raises(DispatchFailedError):
                await bus.drain()


class TestDeferredDispatch:
    async def test_work_runs_even_when_the_ticket_is_never_redeemed(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            bus.dispatch(CreateUser(email="fire@and.forget"))

            await bus.drain()

        assert ledger.entries == ["created fire@and.forget"]

    async def test_done_reports_progress_without_awaiting(
        self, builder: ContainerBuilder, gate: Gate
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Block())

            assert ticket.done() is False
            gate.release()
            await ticket.result()
            assert ticket.done() is True

    async def test_the_repr_says_which_dispatch_and_whether_it_is_done(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(CreateUser(email="a@b.c"))
            await ticket.result()

            assert ticket.id in repr(ticket)
            assert "done" in repr(ticket)
