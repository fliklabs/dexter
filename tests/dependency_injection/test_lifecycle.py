"""Container and scope lifecycle: closing, idempotency, and use after close."""

import pytest

from dexter.dependency_injection import (
    ContainerBuilder,
    ContainerClosedError,
    Scope,
    ScopeClosedError,
)

from .conftest import Db


class TestContainerClose:
    async def test_resolving_after_close_raises(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            await container.resolve(Db)

    async def test_creating_a_scope_after_close_raises(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            container.scope()

    async def test_close_is_idempotent(self, builder: ContainerBuilder) -> None:
        container = builder.build()
        await container.aclose()
        await container.aclose()  # must not raise

    async def test_the_container_is_usable_as_a_context_manager(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()

        async with container as entered:
            assert isinstance(await entered.resolve(Db), Db)

        with pytest.raises(ContainerClosedError):
            await container.resolve(Db)


class TestScopeClose:
    async def test_resolving_after_the_scope_exits_raises_scope_closed(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            await scope.resolve(Db)

        with pytest.raises(ScopeClosedError):
            await scope.resolve(Db)

    async def test_closing_a_scope_leaves_the_container_usable(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()

        async with container.scope() as scope:
            await scope.resolve(Db)

        assert isinstance(await container.resolve(Db), Db)

    async def test_a_scope_error_is_distinguishable_from_a_container_error(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        scope = container.scope()
        await scope.aclose()

        # Both descend from ContainerStateError, but the specific type says which closed.
        with pytest.raises(ScopeClosedError):
            await scope.resolve(Db)


class TestClosingDetails:
    """Remaining lifecycle branches: idempotency on scopes, and what close does not guard."""

    async def test_closing_a_scope_twice_is_idempotent(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        scope = container.scope()
        await scope.aclose()
        await scope.aclose()  # must not raise

    async def test_exiting_a_scope_after_closing_it_by_hand_is_idempotent(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        async with container.scope() as scope:
            await scope.aclose()  # __aexit__ then closes an already-closed scope

    async def test_a_scope_closes_even_when_its_body_raises(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Scoped)
        container = builder.build()

        scope = container.scope()

        async def use_then_fail() -> None:
            async with scope:
                await scope.resolve(Db)
                raise RuntimeError("body failed")

        with pytest.raises(RuntimeError, match="body failed"):
            await use_then_fail()

        # __aexit__ ignores the exception arguments and closes regardless.
        with pytest.raises(ScopeClosedError):
            await scope.resolve(Db)

    async def test_is_registered_still_answers_after_close(
        self, builder: ContainerBuilder
    ) -> None:
        # Deliberately not guarded: asking what a container was configured with is harmless
        # after close, and callers use it while wiring diagnostics.
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()
        await container.aclose()

        assert container.is_registered(Db)

    async def test_entering_an_already_closed_container_defers_the_error_to_resolve(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()
        await container.aclose()

        async with container as entered:
            with pytest.raises(ContainerClosedError):
                await entered.resolve(Db)
