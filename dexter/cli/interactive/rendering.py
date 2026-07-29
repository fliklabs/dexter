"""Terminal setup, the pieces every screen draws, and reading a key.

Importing this module imports `curses`, which is not available on every platform. Nothing
outside `dexter.cli.interactive` imports it, and `dexter.cli.run` only reaches it once it has
established there is a terminal to draw on.
"""

import contextlib
import curses
import os
import sys
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
        enable_mouse()
        yield screen
    finally:
        disable_mouse()
        # Restoration must happen whatever went wrong above. A terminal left in raw mode with
        # no cursor is one the user has to `reset` by hand, and they will not know why.
        show_cursor(visible=True)
        screen.keypad(False)
        curses.noraw()
        curses.nocbreak()
        curses.echo()
        curses.endwin()


DRAG_ON = "\033[?1002h"
DRAG_OFF = "\033[?1002l"
"""Button-event tracking: report motion, but only while a button is held.

Sent by hand because **ncurses does not ask for it**. It enables mouse reporting from the `XM`
terminfo capability, and where that is missing — which is the common case, `xterm-256color`
included — it falls back to a built-in default of mode 1000: presses and releases, no motion at
all. `REPORT_MOUSE_POSITION` surviving in the mask `mousemask` returns says only that ncurses
can *represent* a motion event, not that anything will ever send one.

Mode 1002 rather than 1003, which reports every movement whether or not a button is down: a
drag is the only motion this menu has any use for, and 1003 turns an idle terminal into a
stream of events the loop has to drain.

Nothing is sent to *change* the report format. ncurses decodes the legacy `\\033[M` encoding
here, and asking the terminal for the SGR format it has not been told to expect would turn
every click into a burst of stray keys.
"""


def enable_mouse() -> None:
    """Ask the terminal to report button presses, releases and drags.

    Tolerated when it fails: a terminal without mouse reporting is not a terminal this menu
    should refuse to run in, and everything here is reachable from the keyboard. A terminal that
    does not know mode 1002 ignores the request, and presses and releases still arrive — which
    is why a selection is defined by where the button came up rather than by the motion between.

    **This takes over the terminal's own click-and-drag selection** for as long as the menu is
    up, which is the trade: native selection is wiped every time a live pane redraws, so in the
    one place selection matters most it does not work. Most terminals still give it back while
    Shift is held.

    `mouseinterval(0)` turns off click detection, which otherwise swallows a press and its
    release for a third of a second to decide whether they were a click — long enough that a
    drag begins by feeling broken.
    """
    with contextlib.suppress(curses.error):
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)
    ask(DRAG_ON)


def disable_mouse() -> None:
    """Give the terminal its own mouse back.

    Left on, the terminal keeps sending reports to whatever the user runs next, and they arrive
    as garbage on its standard input.
    """
    ask(DRAG_OFF)
    with contextlib.suppress(curses.error):
        curses.mousemask(0)


def ask(sequence: str) -> None:
    """Put a terminal mode sequence on the wire, flushed there and then.

    `curses.putp` is the obvious way and is the wrong one. It writes into the C stdio buffer
    that ncurses flushes on its own schedule, so the request can still be sitting in it
    unsent — and the menu's whole job is holding a command that may be killed rather than
    asked to leave, which is precisely when an unflushed buffer is lost. `sys.stdout` is a
    buffer this can flush itself.

    Tolerated when it fails, like everything else about the mouse.
    """
    with contextlib.suppress(OSError, ValueError):
        sys.stdout.write(sequence)
        sys.stdout.flush()


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


@contextlib.contextmanager
def polling(screen: Any) -> Iterator[None]:
    """Make `getch` return immediately for the duration, rather than parking the thread.

    This is what lets something run *while* the screen is watched. A blocking `getch` holds the
    only thread there is, so nothing else — no command, no server, no repaint on a timer —
    makes progress until a key arrives. Non-blocking reads turn waiting into a loop the caller
    can put an `await` in.

    Always restored, because every other screen depends on a read that waits.
    """
    screen.nodelay(True)
    try:
        yield
    finally:
        screen.nodelay(False)


_LAYOUT = ("border", "prompt", "gap", "options", "hint", "border")
"""The rows a modal box occupies, named so the arithmetic below reads as a layout."""

_HINT = "←→ choose   Enter confirm   ESC cancel"
_NARROWEST = len(_HINT)
"""No narrower than its own instructions, or the box explains nothing."""

_GAP = 2
"""Columns between the two options."""


class Modal:
    """A two-option prompt drawn over whatever is already on screen.

    Drawing and key handling are separate so one widget serves both kinds of caller: a screen
    that blocks on a key, and a poll loop that cannot. A modal raised over a *running* command
    must not stop it — the command keeps going while the question sits there — and that is only
    possible if the modal never owns the loop.
    """

    __slots__ = ("options", "prompt", "selected")

    def __init__(self, prompt: str, *, deny: str, affirm: str) -> None:
        """Set the question and what the two answers are called."""
        self.prompt = prompt
        self.options = (deny, affirm)
        self.selected = 0

    def draw(self, screen: Any) -> None:
        """Paint a bordered box centred over whatever is already on screen.

        Drawn as a box rather than a couple of lines because it interrupts: it appears over
        output that is still arriving, and a prompt that reads as one more line of that output
        is a prompt people answer by accident. The border is what says "this is asking you
        something". Nothing underneath is erased, so the box floats.
        """
        height, width = screen.getmaxyx()
        labels = [f"  {label}  " for label in self.options]
        content = max(len(self.prompt), sum(len(label) for label in labels) + _GAP)
        inner = max(_NARROWEST, min(content, width - 6))
        box = inner + 4
        top = max(0, height // 2 - len(_LAYOUT) // 2)
        left = max(0, (width - box) // 2)

        write(screen, top, left, f"┌{'─' * (box - 2)}┐", curses.A_BOLD)
        for offset in range(1, len(_LAYOUT) - 1):
            write(screen, top + offset, left, f"│{' ' * (box - 2)}│", curses.A_BOLD)
        write(
            screen, top + len(_LAYOUT) - 1, left, f"└{'─' * (box - 2)}┘", curses.A_BOLD
        )

        write(screen, top + 1, left + 2, self.prompt[:inner], curses.A_BOLD)

        column = left + 2
        for index, label in enumerate(labels):
            attribute = curses.A_REVERSE if index == self.selected else curses.A_NORMAL
            write(screen, top + 3, column, label, attribute)
            column += len(label) + _GAP

        write(screen, top + 4, left + 2, _HINT[:inner], curses.A_DIM)
        screen.refresh()

    def key(self, key: int) -> bool | None:
        """Apply one keypress. Returns the answer once settled, or `None` while still open."""
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("\t")):
            self.selected = 1 - self.selected
            return None
        if key in ENTER:
            return self.selected == 1
        if key == ESC:
            return False
        if key == INTERRUPT:
            # A second Ctrl+C means it. Someone reaching for it twice is not doing so by
            # accident, and making them find the arrow keys first would be obtuse.
            return True
        return None


LINGER = 3.0
"""Seconds a toast stays up. Long enough to read six words, short enough not to be in the way."""


class Toast:
    """A short message that appears in the corner and goes away by itself.

    The same bordered box as `Modal`, and deliberately so — a reader has already learnt that a
    box means "this is the menu talking, not the command". It differs in the two ways that
    matter: it is in the top-right rather than the middle, because it reports rather than
    interrupts, and it answers no question, so nothing has to be dismissed.

    Expiry is asked, not counted: `expired` takes the time rather than reading a clock, so the
    behaviour can be tested without waiting three seconds or patching anything.
    """

    __slots__ = ("message", "until")

    def __init__(self, message: str, *, at: float, seconds: float = LINGER) -> None:
        """Show `message` from `at` until `seconds` later."""
        self.message = message
        self.until = at + seconds

    def expired(self, now: float) -> bool:
        """Whether it has been up long enough."""
        return now >= self.until

    def draw(self, screen: Any) -> None:
        """Paint the box in the top-right, over whatever is there."""
        _, width = screen.getmaxyx()
        inner = min(len(self.message), max(_NARROWEST, width - 8))
        box = inner + 4
        left = max(0, width - box - 2)

        write(screen, 0, left, f"┌{'─' * (box - 2)}┐", curses.A_BOLD)
        write(screen, 1, left, f"│{' ' * (box - 2)}│", curses.A_BOLD)
        write(screen, 2, left, f"└{'─' * (box - 2)}┘", curses.A_BOLD)
        write(screen, 1, left + 2, self.message[:inner], curses.A_BOLD)


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
    modal = Modal("Leave the menu?", deny="Stay", affirm="Quit")
    while True:
        modal.draw(screen)
        try:
            key = screen.getch()
        except KeyboardInterrupt:
            return True
        answer = modal.key(key)
        if answer is not None:
            return answer
