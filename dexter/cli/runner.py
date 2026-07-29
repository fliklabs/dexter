"""Invoking a command: click parses, dexter awaits.

This is where the module's central constraint is honoured. dexter does not drive an event loop
on a caller's behalf, and a CLI has to bridge a synchronous process into an async world — so
the bridge is put in the *caller's* entry point instead. click is invoked with
`standalone_mode=False`, which returns whatever the callback returned rather than exiting the
process; an async callback returns a coroutine, and that coroutine is awaited here. Nothing in
dexter calls `asyncio.run`.

The same function serves both paths. Run from a shell, output goes straight to the terminal.
Run from the menu, a `Capture` redirects it into a buffer that repaints a curses pane on every
write — the command cannot tell the difference.
"""

import asyncio
import contextlib
import inspect
import io
import re
import traceback
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import click

from .console import CliConsole

_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

ABORTED = 130
"""Exit code for a command the user interrupted, by convention 128 + SIGINT."""

FAILED = 1
"""Exit code for a command that raised something nobody expected."""


class Outcome:
    """What happened when a command ran."""

    __slots__ = ("exit_code", "output")

    def __init__(self, exit_code: int, output: str) -> None:
        """Record the exit code and whatever the command wrote, if it was captured."""
        self.exit_code = exit_code
        self.output = output

    @property
    def succeeded(self) -> bool:
        """Whether the command reported success."""
        return self.exit_code == 0


class Capture:
    """Where a command's output goes when it must not reach the terminal.

    The interactive layer passes one of these because curses owns the screen: anything written
    straight to stdout would corrupt the drawing. `on_output` receives everything written so
    far, each time it grows, which is what makes output appear as it happens rather than all at
    once when the command finishes.
    """

    __slots__ = ("console", "on_output")

    def __init__(self, console: CliConsole, on_output: Callable[[str], None]) -> None:
        """Take the console to redirect and the callback to notify."""
        self.console = console
        self.on_output = on_output


async def invoke(
    root: click.Group,
    argv: Sequence[str],
    container: Any,
    *,
    prog_name: str,
    capture: Capture | None = None,
) -> Outcome:
    """Run `argv` against `root` and return how it went.

    Without a `capture` the command writes to the terminal and `Outcome.output` is empty.
    """
    if capture is None:
        return Outcome(await _dispatch(root, argv, container, prog_name), "")

    buffer = _StreamingBuffer(capture.on_output)
    with capture.console.capture(buffer), _redirect(buffer):
        exit_code = await _dispatch(root, argv, container, prog_name)
    return Outcome(exit_code, _ANSI.sub("", buffer.getvalue()))


async def _dispatch(
    root: click.Group, argv: Sequence[str], container: Any, prog_name: str
) -> int:
    """Parse `argv`, then run whatever it selected."""
    try:
        result: Any = root.main(
            list(argv), prog_name=prog_name, standalone_mode=False, obj=container
        )
    except click.exceptions.Exit as finished:
        # `--help` and friends. Not a failure; click has already printed what it wanted.
        return int(finished.exit_code)
    except click.ClickException as error:
        error.show()
        return int(error.exit_code)
    except click.exceptions.Abort:
        return ABORTED

    if not inspect.isawaitable(result):
        return _exit_code_of(result)
    return await _await(result)


async def _await(result: Any) -> int:
    """Await an async command, turning every way it can end into an exit code.

    `inspect.isawaitable` is checked on the *result* rather than `iscoroutinefunction` on the
    callback, because a callback wrapped by `inject` — or by any decorator — still returns a
    coroutine even when the wrapper itself is not one.
    """
    try:
        return _exit_code_of(await result)
    except click.ClickException as error:
        # A command can raise a usage error after parsing: a name it could only check once it
        # was running, say.
        error.show()
        return int(error.exit_code)
    except click.exceptions.Abort, KeyboardInterrupt:
        return ABORTED
    except asyncio.CancelledError:
        # The user stopped it, so this is an outcome rather than a failure — and swallowing
        # the cancellation is deliberate, for the same reason `SystemExit` is swallowed below.
        # It also means the caller still gets everything the command printed before it was
        # stopped, which is the part worth reading.
        return ABORTED
    except SystemExit as exiting:
        # Swallowed rather than allowed through: a command raising `SystemExit(1)` must not
        # take down a menu that has the terminal in raw mode.
        return int(exiting.code or 0)
    except Exception:
        # Rendered rather than escaping. The same reason: an unhandled traceback reaching a
        # raw-mode terminal leaves it unusable and tells the user nothing.
        click.echo(traceback.format_exc(), err=True)
        return FAILED


def _exit_code_of(result: object) -> int:
    """A command may return an exit code; anything else means success."""
    return result if isinstance(result, int) and not isinstance(result, bool) else 0


class _StreamingBuffer(io.StringIO):
    """A buffer that reports its whole contents every time something is written to it."""

    def __init__(self, on_output: Callable[[str], None]) -> None:
        """Take the callback to notify on every write."""
        super().__init__()
        self._on_output = on_output

    def write(self, s: str) -> int:
        """Record `s`, then hand the caller everything written so far, stripped of ANSI."""
        written = super().write(s)
        self._on_output(_ANSI.sub("", self.getvalue()))
        return written


@contextlib.contextmanager
def _redirect(buffer: io.StringIO) -> Iterator[None]:
    """Send stdout and stderr into `buffer` too.

    Redirecting the console alone is not enough: `click.echo`, `print`, and anything a
    subprocess helper writes all go to the streams rather than through rich.
    """
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield
