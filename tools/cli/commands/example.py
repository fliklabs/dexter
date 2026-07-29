"""Commands for the reference applications.

`list` discovers what exists. The two run commands are declared separately, each with its own
options, because an option the CLI cannot see cannot be offered in the menu — a passthrough of
arbitrary arguments would make the form screen impossible to draw.

`--section` is a `click.Choice` built from the example's own `SECTIONS`, so the menu offers a
picker rather than a text box and an invalid section cannot be typed at all. That costs
importing the examples when this module loads, which is the trade: a picker that is always
right, against a slightly heavier start.

Examples run **in-process**, so their output streams into the menu's pane exactly as any other
command's does.
"""

import click

from dexter.cli import CliConsole, inject
from dexter.dependency_injection import Container
from examples.storefront.__main__ import SECTIONS as STOREFRONT_SECTIONS
from examples.storefront.__main__ import main as run_storefront
from examples.taskflow.__main__ import SECTIONS as TASKFLOW_SECTIONS
from examples.taskflow.__main__ import main as run_taskflow

from ..paths import example_names

_ALL = "all"


def _sections(names: tuple[str, ...]) -> click.Choice[str]:
    """A choice of one section, or all of them."""
    return click.Choice([_ALL, *names])


@click.command("list")
@inject
async def list_examples(scope: Container) -> None:
    """List the reference applications that can be run."""
    console = await scope.resolve(CliConsole)
    names = example_names()
    if not names:
        console.warn("No reference applications found under `examples/`.")
        return

    table = console.table("Example", "Run it with")
    for name in names:
        table.add_row(f"[cyan]{name}[/]", f"[dim]./dx example {name}[/]")
    console.print(table)
    console.detail(f"{len(names)} example(s)")


@click.command("taskflow")
@click.option(
    "--section",
    type=_sections(tuple(TASKFLOW_SECTIONS)),
    default=_ALL,
    help="Run one part of the walkthrough instead of all of it.",
)
@click.option(
    "--with-notifier",
    is_flag=True,
    help="Bind a Notifier, so the optional dependency is injected rather than None.",
)
@inject
async def taskflow(scope: Container, section: str, with_notifier: bool) -> None:  # noqa: ARG001
    """Dependency injection: scopes, async factories, disposal.

    `scope` is unused: the example builds its own container, which is the point — it has to
    stand alone as something a reader can copy.
    """
    await run_taskflow(section=section, with_notifier=with_notifier)


@click.command("storefront")
@click.option(
    "--section",
    type=_sections(tuple(STOREFRONT_SECTIONS)),
    default=_ALL,
    help="Run one part of the walkthrough instead of all of it.",
)
@inject
async def storefront(scope: Container, section: str) -> None:  # noqa: ARG001 - see above
    """CQRS: commands, queries, events, tickets, settling."""
    await run_storefront(section=section)
