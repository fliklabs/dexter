"""Running the reference service as a worker.

The other half of `refapp web`. Both build the same container from the same `MODULES`; this one
resolves the buses and does the work directly instead of putting a socket in front of it,
which is the whole claim a module list makes — one description of a service, two ways to run
it.

`--section` is a `click.Choice` built from the walkthrough's own `SECTIONS`, so the menu offers
a picker rather than a text box and an invalid section cannot be typed at all. That costs
importing the example when this module loads: the trade is a picker that is always right
against a slightly heavier start.

It runs **in-process**, so its output streams into the menu's pane exactly as any other
command's does — and, like any other command, Ctrl+C can stop it partway.
"""

import click

from dexter.cli import inject
from dexter.dependency_injection import Container
from examples.storefront.__main__ import SECTIONS
from examples.storefront.__main__ import main as run_worker

_ALL = "all"


@click.command("worker")
@click.option(
    "--section",
    type=click.Choice([_ALL, *SECTIONS]),
    default=_ALL,
    help="Run one part of the walkthrough instead of all of it.",
)
@inject
async def worker(scope: Container, section: str) -> None:  # noqa: ARG001
    """Run the reference service as a worker: no socket, no routes, same modules.

    `scope` is this command's own container and is unused. The service builds its own, which is
    the point — it has to stand alone as something a reader can copy.
    """
    await run_worker(section=section)
