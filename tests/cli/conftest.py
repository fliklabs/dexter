"""A small command tree, and the container that hosts it.

Everything here is ordinary click and ordinary dexter — there is no test harness, because the
point of the module is that a CLI is just commands registered into a container.
"""

import click
import pytest

from dexter.cli import CliConsole, inject, register_command, use_cli
from dexter.dependency_injection import Container, ContainerBuilder


class Ledger:
    """Records what commands did, so tests assert on behaviour rather than output."""

    def __init__(self) -> None:
        self.entries: list[str] = []


@click.command("greet")
@click.option("--name", default="world", help="Who to greet.")
@click.option("--shout", is_flag=True, help="Upper-case it.")
@inject
async def greet(scope: Container, name: str, shout: bool) -> None:
    """Say hello."""
    ledger = await scope.resolve(Ledger)
    ledger.entries.append(name.upper() if shout else name)


@click.command("count")
@click.argument("upto", type=int)
@inject
async def count(scope: Container, upto: int) -> None:
    """Count up to a number."""
    ledger = await scope.resolve(Ledger)
    ledger.entries.extend(str(number) for number in range(upto))


@click.command("speak")
@inject
async def speak(scope: Container) -> None:
    """Write something to the console."""
    console = await scope.resolve(CliConsole)
    console.ok("spoken")


@click.command("fail")
@inject
async def fail(scope: Container) -> None:
    """Always fail."""
    raise RuntimeError("this command is meant to fail")


@click.command("exits")
@inject
async def exits(scope: Container) -> int:
    """Return a non-zero exit code without raising."""
    return 3


@click.command("depart")
@inject
async def depart(scope: Container) -> None:
    """Raise SystemExit, which must not take the process down."""
    raise SystemExit(4)


@click.command("choose")
@click.option("--mode", type=click.Choice(["fast", "slow"]), default="fast")
@click.option("--needed", required=True, help="A required option.")
@inject
async def choose(scope: Container, mode: str, needed: str) -> None:
    """A command with a choice and a required option, for the form."""
    ledger = await scope.resolve(Ledger)
    ledger.entries.append(f"{mode}:{needed}")


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture
def builder(ledger: Ledger) -> ContainerBuilder:
    """A builder with the CLI wired and the sample tree registered."""
    container_builder = ContainerBuilder()
    container_builder.register(Ledger).to_instance(ledger)
    use_cli(container_builder)
    register_command(container_builder, greet)
    register_command(container_builder, speak)
    register_command(container_builder, fail)
    register_command(container_builder, exits)
    register_command(container_builder, depart)
    register_command(container_builder, count, group="numbers", help="Number things.")
    register_command(container_builder, choose, group="numbers")
    return container_builder


@pytest.fixture
def bare_builder() -> ContainerBuilder:
    """A builder with nothing wired."""
    return ContainerBuilder()
