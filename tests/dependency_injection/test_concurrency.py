"""The concurrency guarantee.

These are the tests that justify the design: a per-key in-flight `asyncio.Task` awaited through
`asyncio.shield`, with no lock anywhere. A simpler `asyncio.Future` map passes the first test
here but fails `test_cancelling_the_creating_resolver_does_not_affect_other_waiters`, and a
single `asyncio.Lock` deadlocks on nested resolution.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import Db

CONCURRENT_RESOLVERS = 50


def drive_once[T](coroutine: Coroutine[Any, Any, T]) -> tuple[T | None, bool]:
    """Advance `coroutine` one step, returning its result and whether it suspended.

    A coroutine that finishes on the first `send` never awaited anything that suspends, which
    is how "this resolution created no task and spent no event-loop turn" is asserted.
    """
    try:
        coroutine.send(None)
    except StopIteration as completed:
        result: T = completed.value
        return result, False
    coroutine.close()
    return None, True


class TestSingleInstanceUnderConcurrency:
    async def test_a_suspending_factory_runs_once_for_many_concurrent_resolvers(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_db() -> Db:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)  # yield, so resolvers genuinely interleave
            return Db()

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        results = await asyncio.gather(
            *(container.resolve(Db) for _ in range(CONCURRENT_RESOLVERS))
        )

        assert calls == 1
        assert len({id(result) for result in results}) == 1

    async def test_nested_resolution_does_not_deadlock(
        self, builder: ContainerBuilder
    ) -> None:
        class Level3:
            def __init__(self) -> None: ...

        class Level2:
            def __init__(self, dependency: Level3) -> None:
                self.dependency = dependency

        class Level1:
            def __init__(self, dependency: Level2) -> None:
                self.dependency = dependency

        for cls in (Level3, Level2, Level1):
            builder.register(cls).to(cls, scope=Scope.Singleton)
        container = builder.build()

        # A single lock around resolution would hang here; the timeout makes that a failure
        # rather than a hung suite.
        async with asyncio.timeout(5):
            assert isinstance(await container.resolve(Level1), Level1)


class TestFailurePropagation:
    async def test_every_waiter_sees_the_failure_and_the_factory_runs_once(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_db() -> Db:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            raise RuntimeError("factory failed")

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        outcomes = await asyncio.gather(
            *(container.resolve(Db) for _ in range(5)), return_exceptions=True
        )

        assert calls == 1
        assert {type(outcome) for outcome in outcomes} == {RuntimeError}

    async def test_a_later_resolve_retries_rather_than_replaying_the_failure(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_db() -> Db:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient failure")
            return Db()

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        with pytest.raises(RuntimeError):
            await container.resolve(Db)

        assert isinstance(await container.resolve(Db), Db)
        assert calls == 2


class TestHotPathDoesNotSuspend:
    """The two-tier design is void if every resolution creates a task.

    Driving the coroutine by hand is the direct way to assert this: if it completes on the
    first `send`, it never suspended, so no task was created and no event-loop turn was spent.
    """

    async def test_a_cached_singleton_resolves_without_suspending(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()
        warmed = await container.resolve(Db)  # first resolve populates the cache

        resolved, suspended = drive_once(container.resolve(Db))
        if suspended:
            pytest.fail("resolving a cached singleton suspended")
        assert resolved is warmed

    async def test_a_transient_with_no_async_provider_resolves_without_suspending(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Transient)
        container = builder.build()

        resolved, suspended = drive_once(container.resolve(Db))
        if suspended:
            pytest.fail("resolving a transient suspended")
        assert isinstance(resolved, Db)


class TestCancellation:
    async def test_cancelling_the_creating_resolver_does_not_affect_other_waiters(
        self, builder: ContainerBuilder
    ) -> None:
        async def open_db() -> Db:
            await asyncio.sleep(0.05)
            return Db()

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        creator = asyncio.create_task(container.resolve(Db))
        await asyncio.sleep(0)
        waiter = asyncio.create_task(container.resolve(Db))
        await asyncio.sleep(0)

        creator.cancel()

        # The waiter never asked to be cancelled, so it must still get its instance.
        async with asyncio.timeout(5):
            assert isinstance(await waiter, Db)


class Session:
    """A scoped dependency, for concurrency tests that involve scopes."""


class TestScopesUnderConcurrency:
    """Every guarantee above must hold per scope, not just on the root."""

    async def test_concurrent_resolvers_in_one_scope_share_one_scoped_instance(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_session() -> Session:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return Session()

        builder.register(Session).to(open_session, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            results = await asyncio.gather(
                *(scope.resolve(Session) for _ in range(CONCURRENT_RESOLVERS))
            )

        assert calls == 1
        assert all(result is results[0] for result in results)

    async def test_two_scopes_resolving_concurrently_get_one_instance_each(
        self, builder: ContainerBuilder
    ) -> None:
        # The core scoped guarantee under concurrency: isolation between scopes, and exactly
        # one construction within each.
        calls = 0

        async def open_session() -> Session:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return Session()

        builder.register(Session).to(open_session, scope=Scope.Scoped)
        container = builder.build()

        async def resolve_many(scope: object) -> list[Session]:
            assert isinstance(scope, type(container))
            return list(
                await asyncio.gather(*(scope.resolve(Session) for _ in range(10)))
            )

        async with container.scope() as first, container.scope() as second:
            first_results, second_results = await asyncio.gather(
                resolve_many(first), resolve_many(second)
            )

        assert calls == 2  # once per scope, not once overall and not once per resolver
        assert all(result is first_results[0] for result in first_results)
        assert all(result is second_results[0] for result in second_results)
        assert first_results[0] is not second_results[0]

    async def test_a_singleton_requested_from_two_scopes_is_built_once(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_db() -> Db:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return Db()

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        async with container.scope() as first, container.scope() as second:
            results = await asyncio.gather(first.resolve(Db), second.resolve(Db))

        assert calls == 1
        assert results[0] is results[1]

    async def test_a_scoped_failure_reaches_every_waiter_and_allows_a_retry(
        self, builder: ContainerBuilder
    ) -> None:
        calls = 0

        async def open_session() -> Session:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            if calls == 1:
                raise RuntimeError("scoped factory failed")
            return Session()

        builder.register(Session).to(open_session, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            outcomes = await asyncio.gather(
                *(scope.resolve(Session) for _ in range(4)), return_exceptions=True
            )
            assert {type(outcome) for outcome in outcomes} == {RuntimeError}
            assert calls == 1

            assert isinstance(await scope.resolve(Session), Session)
            assert calls == 2

    async def test_a_cached_scoped_instance_resolves_without_suspending(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Session).to(Session, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            warmed = await scope.resolve(Session)
            resolved, suspended = drive_once(scope.resolve(Session))
            if suspended:
                pytest.fail("resolving a cached scoped instance suspended")
            assert resolved is warmed

    async def test_nested_resolution_inside_a_scope_does_not_deadlock(
        self, builder: ContainerBuilder
    ) -> None:
        class Inner:
            def __init__(self) -> None: ...

        class Outer:
            def __init__(self, inner: Inner) -> None:
                self.inner = inner

        builder.register(Inner).to(Inner, scope=Scope.Scoped)
        builder.register(Outer).to(Outer, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope, asyncio.timeout(5):
            assert isinstance(await scope.resolve(Outer), Outer)


class TestClosingWhileAResolutionIsInFlight:
    """Closing cancels in-flight construction. Nothing else in the suite reaches this path."""

    async def test_closing_cancels_the_waiting_resolver(
        self, builder: ContainerBuilder
    ) -> None:
        started = asyncio.Event()
        blocked = asyncio.Event()  # never set: the build hangs until it is cancelled

        async def slow_db() -> Db:
            started.set()
            await blocked.wait()
            return Db()

        builder.register(Db).to(slow_db, scope=Scope.Singleton)
        container = builder.build()

        resolving = asyncio.create_task(container.resolve(Db))
        await started.wait()

        await container.aclose()  # cancels the in-flight construction task

        with pytest.raises(asyncio.CancelledError):
            await resolving

    async def test_closing_a_scope_cancels_its_own_in_flight_construction(
        self, builder: ContainerBuilder
    ) -> None:
        # A scoped key's task lives on the scope, so closing the scope cancels it. This is the
        # only route to `_settle`'s cancelled branch: the callback must return without writing
        # to the cache, so a cancelled build leaves nothing half-made behind.
        started = asyncio.Event()
        blocked = asyncio.Event()  # never set: only the first build hangs
        calls = 0

        async def open_session() -> Session:
            nonlocal calls
            calls += 1
            started.set()
            if calls == 1:
                await blocked.wait()  # hangs until cancelled by the close
            return Session()

        builder.register(Session).to(open_session, scope=Scope.Scoped)
        container = builder.build()

        scope = container.scope()
        resolving = asyncio.create_task(scope.resolve(Session))
        await started.wait()
        await scope.aclose()

        with pytest.raises(asyncio.CancelledError):
            await resolving
        assert calls == 1

        # A fresh scope rebuilds from nothing, so the cancelled attempt cached no instance.
        async with container.scope() as replacement:
            assert isinstance(await replacement.resolve(Session), Session)
        assert calls == 2

    async def test_a_singleton_in_flight_outlives_the_scope_that_asked_for_it(
        self, builder: ContainerBuilder
    ) -> None:
        # Deliberate asymmetry with the test above: a singleton belongs to the root, so its
        # construction task is the root's and a closing scope must not cancel it. Another
        # scope is still waiting on that same instance.
        started = asyncio.Event()
        calls = 0

        async def open_db() -> Db:
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.sleep(0.05)
            return Db()

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        first = container.scope()
        resolving = asyncio.create_task(first.resolve(Db))
        await started.wait()
        await first.aclose()  # the scope goes away; the root's task does not

        async with asyncio.timeout(5):
            assert isinstance(await resolving, Db)
        assert calls == 1
        assert await container.resolve(Db) is resolving.result()

    async def test_closing_a_container_with_nothing_in_flight_is_fine(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        await container.aclose()  # the early-return path, with an empty in-flight map
