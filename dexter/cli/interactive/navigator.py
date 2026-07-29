"""The loop: draw a menu, run what was chosen, draw it again.

This is the only async part of the interactive layer, and only because running a command is.
Everything it draws with is synchronous, and it blocks the event loop while waiting for a
keypress — which is correct here. The menu is the whole program while it is on screen, and a
command's own work is awaited between screens, not during them.
"""

from typing import Any

import click

from dexter.dependency_injection import Container

from ..console import CliConsole
from ..models import read_fields, shell_command, to_argv
from ..runner import Capture, invoke
from .menu import Menu
from .rendering import Quit, terminal
from .screens import confirm_screen, list_screen, output_screen, params_screen


class _Session:
    """What every screen in one run of the menu needs, gathered once."""

    __slots__ = ("console", "container", "prog_name", "root", "screen")

    def __init__(
        self,
        screen: Any,
        root: click.Group,
        container: Container,
        console: CliConsole,
        prog_name: str,
    ) -> None:
        """Hold the terminal, the tree, and what running a command needs."""
        self.screen = screen
        self.root = root
        self.container = container
        self.console = console
        self.prog_name = prog_name


async def navigate(
    root: click.Group,
    container: Container,
    *,
    prog_name: str,
    title: str,
) -> int:
    """Show the menu until the user leaves, and return the last command's exit code."""
    console = await container.resolve(CliConsole)
    menu = Menu(root, title)
    exit_code = 0

    with terminal() as screen:
        session = _Session(screen, root, container, console, prog_name)
        try:
            while True:
                if not list_screen(screen, menu):
                    if menu.at_root:
                        return exit_code
                    continue

                command = menu.enter()
                if command is None:
                    # A group was opened, or the level is empty. Redraw.
                    continue

                argv = _collect(screen, command)
                if argv is None:
                    continue

                path = menu.command_path(command)
                if not confirm_screen(screen, shell_command(prog_name, path, argv)):
                    continue

                exit_code = await _run(session, path, argv)
        except Quit:
            return exit_code


def _collect(screen: Any, command: click.Command) -> list[str] | None:
    """Gather a command's arguments, skipping the form when it takes none."""
    fields = read_fields(command)
    if not fields:
        return []
    values = params_screen(screen, command.name or "", fields)
    if values is None:
        return None
    return to_argv(fields, values)


async def _run(session: _Session, path: tuple[str, ...], argv: list[str]) -> int:
    """Run a command with its output painted into a pane as it is produced."""
    title = " ".join(path)

    def repaint(output: str) -> None:
        output_screen(session.screen, f"Running: {title}", output, live=True)

    repaint("")
    outcome = await invoke(
        session.root,
        [*path, *argv],
        session.container,
        prog_name=session.prog_name,
        capture=Capture(session.console, repaint),
    )

    status = "ok" if outcome.succeeded else f"exit {outcome.exit_code}"
    output_screen(session.screen, f"{title} — {status}", outcome.output)
    return outcome.exit_code
