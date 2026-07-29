"""Terminal setup, the pieces every screen draws, and reading a key.

Importing this module imports `curses`, which is not available on every platform. Nothing
outside `dexter.cli.interactive` imports it, and `dexter.cli.run` only reaches it once it has
established there is a terminal to draw on.
"""

import contextlib
import curses
import os
from collections.abc import Iterator
from typing import Any

# ncurses waits about a second after a bare ESC to see whether it is the start of an escape
# sequence. Left alone, "ESC goes back" feels broken. Set before any curses call.
os.environ.setdefault("ESCDELAY", "25")

ESC = 27
INTERRUPT = 3
"""Ctrl+C. `curses.raw()` delivers it as a byte rather than raising `KeyboardInterrupt`."""

ENTER = (curses.KEY_ENTER, ord("\n"), ord("\r"))
BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)

REDRAW = -1
"""Returned by `read_key` when a modal consumed the keypress; the caller should redraw."""


class Quit(Exception):  # noqa: N818 - a control signal, not a failure
    """Raised to unwind out of the menu when the user confirms they want to leave."""


@contextlib.contextmanager
def terminal() -> Iterator[Any]:
    """Put the terminal into raw mode for the duration, and always restore it.

    `curses.wrapper` would do this, but it takes a callback and calls it synchronously — and
    the navigator has to `await` between screens. So the setup is done by hand.
    """
    screen = curses.initscr()
    try:
        curses.noecho()
        curses.cbreak()
        # `raw` rather than `cbreak` alone so Ctrl+C arrives as a key. Otherwise Python
        # raises `KeyboardInterrupt` from inside a draw and the terminal is left in raw mode.
        curses.raw()
        show_cursor(visible=False)
        with contextlib.suppress(curses.error):
            curses.start_color()
            curses.use_default_colors()
        screen.keypad(True)
        yield screen
    finally:
        # Restoration must happen whatever went wrong above. A terminal left in raw mode with
        # no cursor is one the user has to `reset` by hand, and they will not know why.
        show_cursor(visible=True)
        screen.keypad(False)
        curses.noraw()
        curses.nocbreak()
        curses.echo()
        curses.endwin()


def show_cursor(*, visible: bool) -> None:
    """Show or hide the cursor, tolerating a terminal that cannot.

    `curs_set` raises on a terminal without the capability, and losing the whole menu over a
    cosmetic detail would be a poor trade.
    """
    with contextlib.suppress(curses.error):
        curses.curs_set(1 if visible else 0)


def write(screen: Any, row: int, column: int, text: str, attribute: int = 0) -> None:
    """Draw `text`, clipped to the window.

    Every write goes through here because curses raises if anything reaches the last cell of
    the last line, and a menu that crashes when the window is small is not worth having.
    """
    height, width = screen.getmaxyx()
    if row < 0 or row >= height or column >= width:
        return
    with contextlib.suppress(curses.error):
        screen.addstr(row, column, text[: max(0, width - column - 1)], attribute)


def header(screen: Any, text: str) -> None:
    """Draw the breadcrumb line."""
    _, width = screen.getmaxyx()
    write(screen, 0, 0, text.ljust(width - 1), curses.A_BOLD | curses.A_UNDERLINE)


def footer(screen: Any, text: str) -> None:
    """Draw the key hints along the bottom."""
    height, width = screen.getmaxyx()
    write(screen, height - 1, 0, text.ljust(width - 1), curses.A_DIM)


def body_height(screen: Any) -> int:
    """How many rows a screen has between the header and the footer."""
    height, _ = screen.getmaxyx()
    return max(1, int(height) - 2)


def read_key(screen: Any) -> int:
    """Read one key, handling Ctrl+C rather than letting it escape.

    Returns `REDRAW` when a modal was shown and dismissed, so the caller repaints instead of
    treating it as input. Raises `Quit` when the user confirms they want to leave.
    """
    try:
        key = screen.getch()
    except KeyboardInterrupt:
        key = INTERRUPT
    if key != INTERRUPT:
        return int(key)
    if confirm_quit(screen):
        raise Quit
    return REDRAW


def confirm_quit(screen: Any) -> bool:
    """Ask whether to leave. Returns True to quit.

    Drawn as a modal rather than quitting outright: Ctrl+C is easy to hit by accident, and
    losing a half-filled form to a slip is annoying.
    """
    height, width = screen.getmaxyx()
    row = max(0, height // 2 - 2)
    selected = 0

    while True:
        prompt = "Leave the menu?"
        write(screen, row, 2, prompt.ljust(width - 3), curses.A_BOLD)
        for index, label in enumerate(("Stay", "Quit")):
            attribute = curses.A_REVERSE if index == selected else curses.A_NORMAL
            write(screen, row + 2, 2 + index * 10, f"  {label}  ", attribute)
        footer(screen, "←→ choose   Enter confirm   ESC stay")
        screen.refresh()

        try:
            key = screen.getch()
        except KeyboardInterrupt:
            return True
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("\t")):
            selected = 1 - selected
        elif key in ENTER:
            return selected == 1
        elif key == ESC:
            return False
        elif key == INTERRUPT:
            return True
