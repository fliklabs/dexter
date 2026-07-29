"""A keyboard-navigable CLI, built from commands registered into a container.

This module ships **no commands of its own** — only the interface and the conventions. A
repository registers what it offers and gets both a menu and a scriptable command tree from
the same declarations::

    builder = ContainerBuilder()
    use_cli(builder)
    register_command(builder, storefront, group="example")
    container = builder.build()

    exit_code = await run(container, sys.argv[1:], prog_name="dx")

`run` decides what to do in this order: arguments were given, so parse and run them; no
arguments and a terminal, so open the menu; no arguments and no terminal, so print help. The
menu is a shell over the same tree, never a separate path — so anything reachable by hand is
reachable from a script, and CI never touches the interactive layer at all.

**Nothing here starts an event loop.** `run` is a coroutine and the caller awaits it, which is
what keeps the library free of `asyncio.run` while still driving a synchronous process. The
three-line entry point that does start one belongs to the consumer.
"""

import sys
from collections.abc import Sequence

import click

from dexter.dependency_injection import Container

from .console import ACCENT as ACCENT
from .console import DETAIL as DETAIL
from .console import ERROR as ERROR
from .console import OK as OK
from .console import WARN as WARN
from .console import CliConsole as CliConsole
from .errors import CliError as CliError
from .errors import CliNotWiredError as CliNotWiredError
from .errors import CliRegistrationError as CliRegistrationError
from .errors import DuplicateCommandError as DuplicateCommandError
from .errors import InteractiveUnavailableError as InteractiveUnavailableError
from .errors import InvalidCommandError as InvalidCommandError
from .models import Field as Field
from .models import FieldKind as FieldKind
from .models import describe_command as describe_command
from .models import describe_kind as describe_kind
from .models import help_text as help_text
from .models import read_fields as read_fields
from .models import shell_command as shell_command
from .runner import Capture as Capture
from .runner import Outcome as Outcome
from .runner import invoke as invoke
from .tree import CommandTree as CommandTree
from .use import inject as inject
from .use import register_command as register_command
from .use import use_cli as use_cli


async def run(
    container: Container,
    argv: Sequence[str] = (),
    *,
    prog_name: str = "dexter",
    title: str | None = None,
) -> int:
    """Run the CLI and return the process exit code.

    Args:
        container: A container `use_cli` was wired into.
        argv: The arguments, usually `sys.argv[1:]`. Empty opens the menu.
        prog_name: The command a user types, used in help and in the shell command the menu
            shows before running anything.
        title: Heading for the menu. Defaults to `prog_name`.

    Never raises for an ordinary failure — a bad argument, an unknown command, or a command
    that itself failed all come back as a non-zero exit code, because that is what a shell
    expects and what a menu can render without tearing down the terminal.
    """
    registry = await container.resolve(CommandTree)
    root = registry.root

    if argv:
        outcome = await invoke(root, argv, container, prog_name=prog_name)
        return outcome.exit_code

    if not _is_interactive():
        # Piped, redirected, or running under CI. The menu cannot work here, and failing
        # inside curses would report a broken installation rather than the real reason.
        click.echo(help_text(root, prog_name))
        return 0

    # Imported here, not at module scope: `curses` is not available on every platform, and
    # everything above this line has to keep working where it is not.
    from .interactive import navigate  # noqa: PLC0415

    return await navigate(
        root, container, prog_name=prog_name, title=title or prog_name
    )


def _is_interactive() -> bool:
    """Whether there is a terminal to draw a menu on."""
    return sys.stdin.isatty() and sys.stdout.isatty()
