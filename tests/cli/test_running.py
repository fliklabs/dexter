"""Running commands: dispatch, injection, exit codes, and what failure does."""

import click
import pytest

from dexter.cli import CliNotWiredError, inject, register_command, run
from dexter.dependency_injection import (
    Container,
    ContainerBuilder,
    ContainerClosedError,
    Scope,
)

from .conftest import Ledger, greet


class Probe:
    """Something scoped, so a test can tell whether its scope was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class TestDispatch:
    async def test_runs_the_command_named_in_argv(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        container = builder.build()
        try:
            assert await run(container, ["greet", "--name", "dexter"]) == 0
        finally:
            await container.aclose()

        assert ledger.entries == ["dexter"]

    async def test_applies_a_flag(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        container = builder.build()
        try:
            await run(container, ["greet", "--name", "dexter", "--shout"])
        finally:
            await container.aclose()

        assert ledger.entries == ["DEXTER"]

    async def test_runs_a_command_inside_a_group(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        container = builder.build()
        try:
            await run(container, ["numbers", "count", "3"])
        finally:
            await container.aclose()

        assert ledger.entries == ["0", "1", "2"]

    async def test_uses_a_default_when_an_option_is_omitted(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        container = builder.build()
        try:
            await run(container, ["greet"])
        finally:
            await container.aclose()

        assert ledger.entries == ["world"]


class TestExitCodes:
    async def test_an_unknown_command_is_a_usage_error(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            assert await run(container, ["nope"]) == 2
        finally:
            await container.aclose()

    async def test_a_bad_option_is_a_usage_error(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            assert await run(container, ["greet", "--nope"]) == 2
        finally:
            await container.aclose()

    async def test_a_command_may_return_its_own_exit_code(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            assert await run(container, ["exits"]) == 3
        finally:
            await container.aclose()

    async def test_a_raising_command_reports_failure_rather_than_escaping(
        self, builder: ContainerBuilder
    ) -> None:
        """It must not propagate: the menu would be holding the terminal in raw mode."""
        container = builder.build()
        try:
            assert await run(container, ["fail"]) == 1
        finally:
            await container.aclose()

    async def test_system_exit_becomes_an_exit_code(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            assert await run(container, ["depart"]) == 4
        finally:
            await container.aclose()

    async def test_help_succeeds(self, builder: ContainerBuilder) -> None:
        container = builder.build()
        try:
            assert await run(container, ["--help"]) == 0
        finally:
            await container.aclose()


class TestInjection:
    async def test_a_command_receives_a_scope(
        self, builder: ContainerBuilder, ledger: Ledger
    ) -> None:
        """Resolving `Ledger` at all proves the scope is a real container scope."""
        container = builder.build()
        try:
            await run(container, ["greet"])
        finally:
            await container.aclose()

        assert ledger.entries

    async def test_the_scope_is_closed_when_the_command_finishes(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Probe).to(Probe, scope=Scope.SCOPED, dispose=Probe.aclose)
        probes: list[Probe] = []

        @click.command("probe")
        @inject
        async def probe(scope: Container) -> None:
            """Resolve something scoped."""
            probes.append(await scope.resolve(Probe))

        register_command(builder, probe)
        container = builder.build()
        try:
            await run(container, ["probe"])
        finally:
            await container.aclose()

        assert len(probes) == 1
        assert probes[0].closed is True, "the command's scope was never closed"

    async def test_each_run_gets_its_own_scope(self, builder: ContainerBuilder) -> None:
        builder.register(Probe).to(Probe, scope=Scope.SCOPED)
        # The objects, not their ids: CPython reuses an address once an object is collected,
        # so comparing ids here would compare two live objects against one recycled slot.
        seen: list[Probe] = []

        @click.command("probe")
        @inject
        async def probe(scope: Container) -> None:
            """Resolve something scoped."""
            seen.append(await scope.resolve(Probe))

        register_command(builder, probe)
        container = builder.build()
        try:
            await run(container, ["probe"])
            await run(container, ["probe"])
        finally:
            await container.aclose()

        assert seen[0] is not seen[1]

    async def test_inject_outside_the_cli_says_so(self) -> None:
        with pytest.raises(CliNotWiredError, match="dexter.cli.run"):
            await greet.callback(name="x", shout=False)  # type: ignore[misc]


class TestClosedContainer:
    async def test_running_against_a_closed_container_raises(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            await run(container, ["greet"])
