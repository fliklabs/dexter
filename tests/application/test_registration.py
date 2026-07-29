"""Registering modules into an application, and what a mistake reports."""

import functools
from typing import Any

import pytest

from dexter.application import (
    ApplicationNotWiredError,
    DuplicateModuleError,
    InvalidModuleError,
    ModuleRegistry,
    describe_module,
    register_module,
    use_application,
)
from dexter.dependency_injection import ContainerBuilder

from .conftest import use_billing, use_rates


class TestWiringOrder:
    def test_registering_a_module_first_names_the_missing_call(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(ApplicationNotWiredError, match="use_application"):
            register_module(builder, use_rates)


class TestWhatIsRecorded:
    def test_records_every_module_in_registration_order(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)
        register_module(builder, use_rates)
        register_module(builder, use_billing)

        registry = builder.resolve_instance(ModuleRegistry)
        assert registry.names() == ("use_rates", "use_billing")
        assert len(registry) == 2

    def test_an_application_starts_with_no_modules(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)
        registry = builder.resolve_instance(ModuleRegistry)

        assert registry.modules() == ()
        assert len(registry) == 0

    def test_reads_as_the_modules_it_holds(self, builder: ContainerBuilder) -> None:
        use_application(builder)
        register_module(builder, use_rates)

        assert "use_rates" in repr(builder.resolve_instance(ModuleRegistry))


class TestGuards:
    def test_rejects_the_same_module_twice(self, builder: ContainerBuilder) -> None:
        use_application(builder)
        register_module(builder, use_rates)

        with pytest.raises(DuplicateModuleError, match="already registered"):
            register_module(builder, use_rates)

    def test_rejects_something_that_is_not_callable(
        self, builder: ContainerBuilder
    ) -> None:
        use_application(builder)
        target: Any = "rates"

        with pytest.raises(InvalidModuleError, match="a function taking the builder"):
            register_module(builder, target)

    def test_a_module_that_fails_reports_from_inside_itself(
        self, builder: ContainerBuilder
    ) -> None:
        """The reason a module is run now rather than collected for later."""

        def use_broken(target: ContainerBuilder) -> None:
            raise RuntimeError("this module is wrong")

        use_application(builder)
        with pytest.raises(RuntimeError, match="this module is wrong"):
            register_module(builder, use_broken)


class TestDescribingAModule:
    def test_names_a_function_by_its_name(self) -> None:
        assert describe_module(use_rates) == "use_rates"

    def test_falls_back_for_something_with_no_name(self) -> None:
        partial = functools.partial(use_rates)
        assert "functools.partial" in describe_module(partial)
