"""What `use_cqrs` registers, and the lifetimes it insists on."""

import pytest

from dexter.cqrs import (
    CommandBus,
    CommandRegistry,
    EventBus,
    EventRegistry,
    InProcessCommandBus,
    InProcessEventBus,
    InProcessQueryBus,
    MiddlewarePipeline,
    QueryBus,
    QueryRegistry,
    register_command_handler,
    use_cqrs,
)
from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
    Scope,
    ScopeRequiredError,
)

from .conftest import CreateUser, CreateUserHandler, running


class TestWhatIsRegistered:
    def test_registers_the_three_registries_and_the_pipeline(
        self, bare_builder: ContainerBuilder
    ) -> None:
        use_cqrs(bare_builder)

        for key in (CommandRegistry, QueryRegistry, EventRegistry, MiddlewarePipeline):
            assert bare_builder.is_registered(key)

    def test_registers_the_three_buses_as_abstract_keys(
        self, bare_builder: ContainerBuilder
    ) -> None:
        use_cqrs(bare_builder)

        for key in (CommandBus, QueryBus, EventBus):
            assert bare_builder.is_registered(key)

    def test_calling_use_cqrs_twice_is_rejected(
        self, bare_builder: ContainerBuilder
    ) -> None:
        use_cqrs(bare_builder)

        with pytest.raises(DuplicateRegistrationError):
            use_cqrs(bare_builder)

    async def test_the_abstract_keys_resolve_to_the_in_process_buses(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            assert isinstance(await scope.resolve(CommandBus), InProcessCommandBus)
            assert isinstance(await scope.resolve(QueryBus), InProcessQueryBus)
            assert isinstance(await scope.resolve(EventBus), InProcessEventBus)


class TestBusLifetime:
    async def test_a_bus_cannot_be_resolved_from_the_root(
        self, builder: ContainerBuilder
    ) -> None:
        """A singleton bus would capture the root and resolve every handler there."""
        container = builder.build()
        try:
            with pytest.raises(ScopeRequiredError):
                await container.resolve(CommandBus)
        finally:
            await container.aclose()

    async def test_one_bus_per_scope(self, builder: ContainerBuilder) -> None:
        container = builder.build()
        try:
            async with container.scope() as first:
                a = await first.resolve(CommandBus)
                b = await first.resolve(CommandBus)
                assert a is b

            async with container.scope() as second:
                c = await second.resolve(CommandBus)

            assert a is not c
        finally:
            await container.aclose()

    async def test_a_bus_resolves_handlers_from_its_own_scope(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.SCOPED
        )
        container = builder.build()
        try:
            async with container.scope() as scope:
                bus = await scope.resolve(CommandBus)
                await bus.dispatch(CreateUser(email="a@b.c")).result()
                handler = await scope.resolve(CreateUserHandler)

            async with container.scope() as other:
                other_handler = await other.resolve(CreateUserHandler)

            assert handler is not other_handler
        finally:
            await container.aclose()


class TestSharedRegistries:
    async def test_every_scope_sees_the_same_registrations(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        container = builder.build()
        try:
            async with container.scope() as first:
                bus = await first.resolve(CommandBus)
                assert await bus.dispatch(CreateUser(email="a@b.c")).result() == 7

            async with container.scope() as second:
                bus = await second.resolve(CommandBus)
                assert await bus.dispatch(CreateUser(email="d@e.f")).result() == 7
        finally:
            await container.aclose()
