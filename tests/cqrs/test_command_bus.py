"""The command bus: tickets, results, and what fails when."""

import pytest

from dexter.cqrs import (
    BusClosedError,
    CommandBus,
    UnhandledCommandError,
    register_command_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    Block,
    BlockHandler,
    CreateUser,
    CreateUserHandler,
    Explode,
    ExplodeHandler,
    Gate,
    Ledger,
    running,
)


def wire(builder: ContainerBuilder) -> ContainerBuilder:
    register_command_handler(
        builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
    )
    register_command_handler(builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT)
    register_command_handler(builder, Block, BlockHandler, scope=Scope.TRANSIENT)
    return builder


class TestHappyPath:
    async def test_returns_the_handlers_result(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)

            assert await bus.dispatch(CreateUser(email="a@b.c")).result() == 7

    async def test_runs_the_handler(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.dispatch(CreateUser(email="a@b.c")).result()

        assert ledger.entries == ["created a@b.c"]

    async def test_redeeming_twice_yields_the_same_result(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(CreateUser(email="a@b.c"))

            assert await ticket.result() == 7
            assert await ticket.result() == 7

        assert ledger.entries == ["created a@b.c"], "the handler ran more than once"

    async def test_the_ticket_carries_an_id_before_the_work_finishes(
        self, builder: ContainerBuilder, gate: Gate
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Block())

            assert isinstance(ticket.id, str)
            assert ticket.id
            assert ticket.done() is False

            gate.release()
            assert await ticket.result() == 99

    async def test_a_dispatch_starts_its_own_correlation_chain(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(CreateUser(email="a@b.c"))
            await ticket.result()

            assert ticket.correlation_id == ticket.id
            assert ticket.envelope.causation_id is None

    async def test_caused_by_continues_the_chain(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            first = bus.dispatch(CreateUser(email="a@b.c"))
            await first.result()

            second = bus.dispatch(CreateUser(email="b@c.d"), caused_by=first.envelope)
            await second.result()

            assert second.correlation_id == first.correlation_id
            assert second.envelope.causation_id == first.id
            assert second.id != first.id

    async def test_the_same_command_dispatched_twice_gets_two_ids(
        self, builder: ContainerBuilder
    ) -> None:
        command = CreateUser(email="a@b.c")
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            first = bus.dispatch(command)
            second = bus.dispatch(command)
            await first.result()
            await second.result()

            assert first.id != second.id


class TestFailures:
    async def test_the_handlers_exception_surfaces_from_result(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Explode(reason="kaboom"))

            with pytest.raises(RuntimeError, match="kaboom"):
                await ticket.result()

            await bus.drain()

    async def test_an_unhandled_command_raises_at_the_call_site(
        self, builder: ContainerBuilder
    ) -> None:
        """Synchronously, with no ticket to redeem and no task left running."""
        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)

            with pytest.raises(UnhandledCommandError, match="register_command_handler"):
                bus.dispatch(CreateUser(email="a@b.c"))

    async def test_the_unhandled_error_names_the_command(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)

            with pytest.raises(UnhandledCommandError) as caught:
                bus.dispatch(CreateUser(email="a@b.c"))

            assert caught.value.message_type is CreateUser


class TestLifecycle:
    async def test_dispatching_after_close_raises(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.aclose()

            with pytest.raises(BusClosedError, match="command bus is closed"):
                bus.dispatch(CreateUser(email="a@b.c"))

    async def test_closing_twice_is_harmless(self, builder: ContainerBuilder) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            await bus.aclose()
            await bus.aclose()

    async def test_the_bus_works_as_an_async_context_manager(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            async with bus:
                assert await bus.dispatch(CreateUser(email="a@b.c")).result() == 7

            with pytest.raises(BusClosedError):
                bus.dispatch(CreateUser(email="a@b.c"))

    async def test_closing_cancels_work_still_running(
        self, builder: ContainerBuilder, ledger: Ledger, gate: Gate
    ) -> None:
        async with running(wire(builder)) as scope:
            bus = await scope.resolve(CommandBus)
            ticket = bus.dispatch(Block())
            await gate.arrived.wait()

            await bus.aclose()

            assert ticket.task.cancelled()
        assert "unblocked" not in ledger.entries


class TestHandlerLifetime:
    async def test_a_transient_handler_is_built_for_every_dispatch(
        self, builder: ContainerBuilder
    ) -> None:
        wire(builder)
        seen: list[int] = []

        async with running(builder) as scope:
            bus = await scope.resolve(CommandBus)
            for _ in range(2):
                handler = await scope.resolve(CreateUserHandler)
                seen.append(id(handler))
            await bus.dispatch(CreateUser(email="a@b.c")).result()

        assert seen[0] != seen[1]
