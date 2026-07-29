"""What `use_application` wires, and why a module must not wire it itself."""

import pytest

from dexter.api import ErrorMap, ExposureRegistry
from dexter.application import ModuleRegistry, register_module, use_application
from dexter.cqrs import (
    CommandBus,
    CommandRegistry,
    QueryBus,
    QueryRegistry,
    use_cqrs,
)
from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
)

from .conftest import GetBill, TooExpensiveError, use_billing, use_rates


class TestWhatItWires:
    def test_wires_the_cqrs_registries_and_buses(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)

        assert isinstance(builder.resolve_instance(CommandRegistry), CommandRegistry)
        assert isinstance(builder.resolve_instance(QueryRegistry), QueryRegistry)
        assert builder.is_registered(CommandBus)
        assert builder.is_registered(QueryBus)

    def test_wires_the_api_registries_too(self, builder: ContainerBuilder) -> None:
        """Even for a service that serves no HTTP — a module declares what it offers."""
        use_application(builder)

        assert isinstance(builder.resolve_instance(ExposureRegistry), ExposureRegistry)
        assert isinstance(builder.resolve_instance(ErrorMap), ErrorMap)

    def test_wires_the_module_registry(self, builder: ContainerBuilder) -> None:
        use_application(builder)

        assert isinstance(builder.resolve_instance(ModuleRegistry), ModuleRegistry)


class TestItIsCalledOnce:
    def test_calling_it_twice_is_refused(self, builder: ContainerBuilder) -> None:
        use_application(builder)

        with pytest.raises(DuplicateRegistrationError):
            use_application(builder)

    def test_a_module_wiring_the_framework_itself_is_refused(
        self, builder: ContainerBuilder
    ) -> None:
        """The mistake this module exists to make impossible.

        `use_cqrs` and `use_api` bind unconditionally, so a module calling one would work
        alone and fail the moment a second module did the same. Modules call `register_*`
        only.
        """

        def use_greedy(target: ContainerBuilder) -> None:
            use_cqrs(target)

        use_application(builder)
        with pytest.raises(DuplicateRegistrationError):
            register_module(builder, use_greedy)


class TestWhatAModuleContributes:
    async def test_everything_it_registered_is_reachable(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)
        register_module(builder, use_rates)
        register_module(builder, use_billing)
        container = builder.build()

        try:
            registry = builder.resolve_instance(ExposureRegistry)
            assert [record.handler.__name__ for record in registry.records()] == [
                "GetBillApi"
            ]

            async with container.scope() as scope:
                queries = await scope.resolve(QueryBus)
                assert await queries.ask(GetBill(quantity=4)) == 1000
        finally:
            await container.aclose()

    def test_a_modules_error_mapping_lands_in_the_application(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)
        register_module(builder, use_rates)
        register_module(builder, use_billing)

        mapping = builder.resolve_instance(ErrorMap).find(TooExpensiveError("x"))
        assert mapping is not None
        assert mapping.status == 402
