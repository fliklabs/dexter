"""The event bus: many handlers, run concurrently, failing together."""

import pytest

from dexter.cqrs import (
    CqrsError,
    EventBus,
    EventHandlingError,
    register_event_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    FailWithKeyError,
    FailWithValueError,
    FastRecorder,
    Ledger,
    NobodyCares,
    RecordFirst,
    RecordSecond,
    SlowRecorder,
    UserCreated,
    running,
)


class TestFanOut:
    async def test_every_handler_runs(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        register_event_handler(
            builder, UserCreated, RecordSecond, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            await bus.publish(UserCreated(user_id=3)).result()

        assert sorted(ledger.entries) == ["first saw 3", "second saw 3"]

    async def test_handlers_run_concurrently(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        """Both handlers start before either finishes, which sequential running cannot do."""
        register_event_handler(
            builder, UserCreated, SlowRecorder, scope=Scope.TRANSIENT
        )
        register_event_handler(
            builder, UserCreated, FastRecorder, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            await bus.publish(UserCreated(user_id=1)).result()

        assert ledger.entries == ["slow start", "fast start", "slow end", "fast end"]

    async def test_the_ticket_reports_how_many_handlers_it_covers(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        register_event_handler(
            builder, UserCreated, RecordSecond, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(UserCreated(user_id=1))

            assert ticket.handler_count == 2
            await ticket.result()


class TestNobodyListening:
    async def test_publishing_with_no_handlers_is_not_an_error(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(NobodyCares())

            await ticket.result()
            assert ticket.done() is True

    async def test_the_ticket_makes_the_silence_visible(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(NobodyCares())
            await ticket.result()

            assert ticket.handler_count == 0


class TestFailures:
    async def test_one_failing_handler_does_not_stop_the_others(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(UserCreated(user_id=5))

            with pytest.raises(EventHandlingError):
                await ticket.result()

        assert ledger.entries == ["first saw 5"]

    async def test_every_failure_is_reported_together(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )
        register_event_handler(
            builder, UserCreated, FailWithKeyError, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(UserCreated(user_id=5))

            with pytest.raises(EventHandlingError) as caught:
                await ticket.result()

            assert len(caught.value.exceptions) == 2
            assert {type(error) for error in caught.value.exceptions} == {
                ValueError,
                KeyError,
            }

    async def test_the_group_names_the_event(self, builder: ContainerBuilder) -> None:
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            ticket = bus.publish(UserCreated(user_id=5))

            with pytest.raises(EventHandlingError) as caught:
                await ticket.result()

            assert caught.value.event_type is UserCreated
            assert "1 of 1 handlers" in caught.value.args[0]

    async def test_the_group_splits_with_except_star(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )
        register_event_handler(
            builder, UserCreated, FailWithKeyError, scope=Scope.TRANSIENT
        )
        caught: list[str] = []

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            try:
                await bus.publish(UserCreated(user_id=5)).result()
            except* ValueError:
                caught.append("value")
            except* KeyError:
                caught.append("key")

        assert sorted(caught) == ["key", "value"]

    async def test_an_unhandled_arm_is_still_a_cqrs_error(
        self, builder: ContainerBuilder
    ) -> None:
        """`derive` keeps the class, so the remainder stays catchable as a `CqrsError`."""
        register_event_handler(
            builder, UserCreated, FailWithValueError, scope=Scope.TRANSIENT
        )
        register_event_handler(
            builder, UserCreated, FailWithKeyError, scope=Scope.TRANSIENT
        )

        async with running(builder) as scope:
            bus = await scope.resolve(EventBus)
            remainder: BaseException | None = None
            handled: list[BaseException] = []
            try:
                try:
                    await bus.publish(UserCreated(user_id=5)).result()
                except* ValueError as value_arm:
                    handled.append(value_arm)
            except CqrsError as error:
                remainder = error

            assert len(handled) == 1

            assert isinstance(remainder, EventHandlingError)
            assert isinstance(remainder.exceptions[0], KeyError)
