"""The loop: draw a menu, run what was chosen, draw it again.

Everything the menu draws with is synchronous, and while it is waiting for the user it blocks
on a keypress — which is correct. The menu is the whole program while it is on screen.

**A running command is the exception, and the reason this module is async.** It is started as a
task and watched rather than awaited outright, with the screen in non-blocking mode and a short
sleep each turn. That is what keeps Ctrl+C reachable while something is running: without it the
only thread there is sits inside `getch`, no key is read for as long as the command lasts, and
anything that does not finish on its own — a server, a watch loop — can never be stopped.

The cost is a poll rather than an interrupt, which for a keypress nobody can type faster than
`_TICK` is not a cost at all.
"""

import asyncio
from typing import Any

import click

from dexter.dependency_injection import Container

from ..console import CliConsole
from ..models import read_fields, shell_command, to_argv
from ..runner import Capture, Outcome, invoke
from .menu import Menu
from .rendering import INTERRUPT, Modal, Quit, polling, terminal
from .screens import confirm_screen, list_screen, output_screen, params_screen

_TICK = 0.03
"""Seconds between polls while a command runs.

Short enough that a keypress feels immediate, long enough that watching costs nothing. The
sleep is not a delay — it is what hands the event loop back so the command can make progress.
"""


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
                    return exit_code

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
    """Run a command, painting its output and watching for Ctrl+C while it works."""
    title = " ".join(path)
    latest = ""

    def collect(output: str) -> None:
        # Only records. Painting is the watch loop's job, so a command that produces nothing
        # still gets a screen that responds, and one that floods does not repaint per write.
        nonlocal latest
        latest = output

    task = asyncio.create_task(
        invoke(
            session.root,
            [*path, *argv],
            session.container,
            prog_name=session.prog_name,
            capture=Capture(session.console, collect),
        )
    )
    outcome = await _watch(session.screen, task, title, lambda: latest)

    status = "ok" if outcome.succeeded else f"exit {outcome.exit_code}"
    output_screen(session.screen, f"{title} — {status}", outcome.output)
    return outcome.exit_code


async def _watch(
    screen: Any,
    task: asyncio.Task[Outcome],
    title: str,
    output: Any,
) -> Outcome:
    """Watch a running command until it finishes or the user stops it.

    The modal is drawn from inside this loop rather than by a blocking helper, so the command
    keeps running while the question is on screen. Stopping something should not require the
    thing to already be stopped.
    """
    modal: Modal | None = None
    painted = None

    with polling(screen):
        while True:
            # Wait on the command *first*, and only look at the keyboard once it has had its
            # turn and not finished. `asyncio.wait` returns the moment the task completes, so
            # a command that ends immediately never reaches the poll below — which matters,
            # because a key read here is a key swallowed, and a fast command must not eat the
            # keystroke meant for the screen that follows it.
            finished, _ = await asyncio.wait({task}, timeout=_TICK)
            if finished:
                break

            key = _poll(screen)

            if modal is not None:
                answer = None if key == _NOTHING else modal.key(key)
                if answer is None:
                    modal.draw(screen)
                    continue
                modal, painted = None, None
                if answer:
                    task.cancel()
                continue

            if key == INTERRUPT:
                modal = Modal(f"Stop {title}?", deny="Keep running", affirm="Stop")
                modal.draw(screen)
                continue

            current = output()
            if current != painted:
                output_screen(screen, f"Running: {title}", current, live=True)
                painted = current

    return await task


_NOTHING = -1
"""What a non-blocking `getch` returns when no key is waiting."""


def _poll(screen: Any) -> int:
    """Read a key if one is waiting, or `_NOTHING`.

    Ctrl+C is still caught defensively: raw mode should deliver it as a byte, but a terminal
    that disagrees would otherwise unwind the whole menu from inside a draw.
    """
    try:
        return int(screen.getch())
    except KeyboardInterrupt:
        return INTERRUPT
