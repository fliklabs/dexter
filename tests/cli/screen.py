"""A stand-in for a curses window, so the screens can be tested without a terminal.

Every screen in `dexter.cli.interactive` talks to its window through a handful of methods:
`erase`, `getmaxyx`, `addstr`, `refresh` and `getch`. Implementing those is enough to drive a
whole screen from a script of keypresses and read back what it drew — which is the difference
between the drawing being untestable and merely being tedious to test.
"""

import curses
from collections.abc import Sequence
from typing import Any, NamedTuple


class Report(NamedTuple):
    """A scripted mouse event, in screen coordinates.

    Scripted as one item because that is how a terminal delivers it: `getch` says `KEY_MOUSE`
    and the details are fetched separately. Keeping the two halves together in the script means
    a test never has to interleave them by hand.
    """

    row: int
    column: int
    state: int


def press(row: int, column: int) -> Report:
    """The button going down at a cell."""
    return Report(row, column, curses.BUTTON1_PRESSED)


def drag(row: int, column: int) -> Report:
    """The pointer moving to a cell with the button held."""
    return Report(row, column, curses.REPORT_MOUSE_POSITION)


def wheel_up(row: int = 1, column: int = 0) -> Report:
    """One wheel tick upwards."""
    return Report(row, column, curses.BUTTON4_PRESSED)


def wheel_down(row: int = 1, column: int = 0) -> Report:
    """One wheel tick downwards, as the platform this runs on actually reports it.

    `REPORT_MOUSE_POSITION` and not `BUTTON5_PRESSED`, which does not exist on an ncurses built
    with `NCURSES_MOUSE_VERSION` 1 — macOS included. Measured by feeding the legacy report
    through ncurses rather than assumed; see `rendering.WHEEL_DOWN`. It is deliberately the same
    state `drag` produces, because that collision is the thing worth having a test for.
    """
    return Report(row, column, curses.REPORT_MOUSE_POSITION)


def release(row: int, column: int) -> Report:
    """The button coming up at a cell."""
    return Report(row, column, curses.BUTTON1_RELEASED)


type Key = str | int | Report
"""A scripted event: a name from `KEYS`, a raw character code, or a mouse report."""

KEYS = {
    "up": curses.KEY_UP,
    "down": curses.KEY_DOWN,
    "pgup": curses.KEY_PPAGE,
    "pgdn": curses.KEY_NPAGE,
    "home": curses.KEY_HOME,
    "end": curses.KEY_END,
    "left": curses.KEY_LEFT,
    "right": curses.KEY_RIGHT,
    "tab": ord("\t"),
    "enter": ord("\n"),
    "esc": 27,
    "ctrl-c": 3,
    "backspace": curses.KEY_BACKSPACE,
}
"""Names for the keys a test sends, so a script reads as a sequence of keystrokes."""

NOTHING = -1
"""What a non-blocking read returns when no key is waiting. `curses.ERR`."""


def _code(key: Key) -> int | Report:
    """The curses key code for a scripted keypress. Mouse reports pass through."""
    return KEYS[key] if isinstance(key, str) else key


class OutOfKeysError(Exception):
    """Raised when a screen asks for a key a test never scripted.

    Loud on purpose: a screen that keeps reading is a screen that did not return when the test
    expected it to, and silently feeding it more input would hide that.
    """


class FakeScreen:
    """Records what was drawn and replays a script of keypresses."""

    def __init__(
        self, keys: Sequence[Key] | None = None, *, size: tuple[int, int] = (24, 80)
    ) -> None:
        """Take the keys to replay, and how big the window should claim to be.

        `Sequence` rather than `list` so a script built as `["down"] * 3` is accepted: a
        `list` is invariant, and every caller would otherwise need an annotation.
        """
        self._keys: list[int | Report] = [_code(key) for key in (keys or ())]
        self._report: Report | None = None
        self._size = size
        self._pending: dict[int, str] = {}
        self._polling = False
        self._served = 0
        self.frames: list[list[str]] = []
        self.reads = 0

        self.keys_per_turn = 1
        """How many keystrokes land between two non-blocking polls.

        One by default, because a script is the whole session and a real terminal only holds
        what has actually been typed so far — without this, a caller that drains the buffer
        would swallow every key the test meant for later screens.

        Raise it to model input arriving faster than the loop turns: a held arrow key, a
        wheel, a paste.
        """

    # ── the curses window surface ────────────────────────────────────

    def erase(self) -> None:
        self._pending = {}

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def addstr(self, row: int, column: int, text: str, attribute: int = 0) -> None:
        # Overlays rather than truncating, because that is what curses does: writing into the
        # middle of a row leaves what is on either side of it alone. Truncating would make a
        # box drawn round something appear to lose its right-hand border the moment anything
        # was written inside it.
        existing = self._pending.get(row, "").ljust(column)
        self._pending[row] = existing[:column] + text + existing[column + len(text) :]

    def refresh(self) -> None:
        height = self._size[0]
        self.frames.append([self._pending.get(row, "") for row in range(height)])

    def nodelay(self, flag: bool) -> None:
        self._polling = flag
        self._served = 0

    def getch(self) -> int:
        self.reads += 1
        if self._polling:
            # Watching a running command: an empty buffer is the ordinary case, and the loop
            # is expected to keep asking. Only a *blocking* read that runs out means a screen
            # failed to return, which is what `OutOfKeysError` is for.
            if not self._keys or self._served >= self.keys_per_turn:
                self._served = 0
                return NOTHING
            self._served += 1
            return self._next()
        if not self._keys:
            raise OutOfKeysError("the screen asked for a key the test did not script")
        return self._next()

    def getmouse(self) -> tuple[int, int, int, int, int]:
        # Curses puts this on the module rather than the window; a window that has it is how a
        # test stands in for the terminal. Raises like the real one when nothing was reported.
        if self._report is None:
            raise curses.error("no mouse event is waiting")
        report, self._report = self._report, None
        return (0, report.column, report.row, 0, report.state)

    def keypad(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _next(self) -> int:
        """Take the next scripted event, holding a mouse report back for `getmouse`."""
        item = self._keys.pop(0)
        if isinstance(item, Report):
            self._report = item
            return curses.KEY_MOUSE
        return item

    # ── what a test asserts against ──────────────────────────────────

    @property
    def last(self) -> list[str]:
        """The most recently drawn frame."""
        return self.frames[-1] if self.frames else []

    def text(self) -> str:
        """Everything ever drawn, joined — for asserting that something appeared."""
        return "\n".join(line for frame in self.frames for line in frame)

    def last_text(self) -> str:
        """The most recent frame, joined."""
        return "\n".join(self.last)

    def row_containing(self, needle: str) -> str:
        """The most recent row containing `needle`, or an empty string."""
        for line in reversed(self.last):
            if needle in line:
                return line
        return ""


def fake_curs_set(monkeypatch: Any) -> None:
    """Stop `curs_set` reaching a terminal that is not there.

    `show_cursor` already suppresses `curses.error`, so this only keeps the tests quiet about
    a call that would otherwise fail for an uninteresting reason.
    """
    monkeypatch.setattr(curses, "curs_set", lambda visibility: 0)
