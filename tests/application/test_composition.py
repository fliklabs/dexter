"""How modules reach each other, and what a missing one reports.

The claim under test is that modules compose through the container rather than through
imports: one asks for a *contract*, and whichever module bound it supplies it. Two things
follow, and both are here — registration order does not matter, and leaving a module out is
reported where the dependency is needed rather than when it is registered.
"""

import pytest

from dexter.application import register_module, use_application
from dexter.dependency_injection import ContainerBuilder, UnregisteredDependencyError

from .conftest import Bills, Orphan, Rates, use_billing, use_orphan, use_rates


class TestOrderDoesNotMatter:
    async def test_a_module_resolves_what_a_later_one_provides(self) -> None:
        builder = ContainerBuilder()
        use_application(builder)
        register_module(builder, use_billing)  # needs Rates
        register_module(builder, use_rates)  # provides it, afterwards
        container = builder.build()

        try:
            bills = await container.resolve(Bills)
            assert isinstance(bills.rates, Rates)
        finally:
            await container.aclose()

    async def test_and_what_an_earlier_one_provides(self) -> None:
        builder = ContainerBuilder()
        use_application(builder)
        register_module(builder, use_rates)
        register_module(builder, use_billing)
        container = builder.build()

        try:
            bills = await container.resolve(Bills)
            assert isinstance(bills.rates, Rates)
        finally:
            await container.aclose()


class TestAMissingModule:
    async def test_is_reported_where_the_dependency_is_needed(self) -> None:
        """Nothing checks module dependencies at registration, and nothing needs to."""
        builder = ContainerBuilder()
        use_application(builder)
        register_module(builder, use_billing)  # and deliberately not `use_rates`
        container = builder.build()

        try:
            with pytest.raises(UnregisteredDependencyError) as raised:
                await container.resolve(Bills)
        finally:
            await container.aclose()

        assert "Rates" in str(raised.value)

    async def test_the_chain_names_what_asked_for_it(self) -> None:
        builder = ContainerBuilder()
        use_application(builder)
        register_module(builder, use_orphan)
        container = builder.build()

        try:
            with pytest.raises(UnregisteredDependencyError) as raised:
                await container.resolve(Orphan)
        finally:
            await container.aclose()

        reported = str(raised.value)
        assert "Unprovided" in reported
        assert "Orphan" in reported

    def test_registering_it_is_still_allowed(self) -> None:
        """Composition does not validate; it composes. The container reports."""
        builder = ContainerBuilder()
        use_application(builder)

        register_module(builder, use_billing)  # no complaint, no `use_rates`
