"""The output pane's state: where it is looking, what is selected, and what it is saying.

The navigator draws; this decides. Everything a running command's pane remembers between two
turns of the watch loop lives on `Pane` — the scroll position, a drag in progress, the toast
left over from the last copy — so the loop itself stays a drain-then-draw and the rules about
what a mouse means are testable without a terminal or a task.

Reading input is here too, for a reason that is not obvious: a mouse report arrives as
`KEY_MOUSE` and then has to be *fetched* with `getmouse` before the next `getch`, or it is lost.
Draining the buffer and decoding a mouse event cannot be separated without that ordering
becoming a rule someone has to remember.
"""

import curses
from typing import Any

from .. import clipboard
from .rendering import (
    ESC,
    INTERRUPT,
    WHEEL_LINES,
    Mouse,
    Toast,
    mouse_report,
)
from .screens import clamp, scrolled
from .selection import Point, Selection

HEADER = 1
"""Rows above the output. The pane starts under the breadcrumb line."""

FASTEST = 3
"""Lines per turn when a drag is held past an edge, at its quickest.

Auto-scroll accelerates with how far past the edge the pointer is, which is what makes it feel
aimed rather than switched on. The cap is what stops a pointer parked at the bottom of a tall
window from flinging the view past everything the reader was dragging towards.
"""

COPIED = "Copied to clipboard"
UNCOPIED = "Could not reach the clipboard"


type Event = int | Mouse
"""What one turn of input produces: a key code, or a mouse report."""


class Pane:
    """What the reader is looking at, and what they are dragging across it.

    A selection is kept in document coordinates by `Selection`, and the pane is what maps a
    screen row onto a line so it can be. The two together are why output arriving mid-drag does
    not drag the highlight along with it.
    """

    __slots__ = ("drag", "drift", "following", "offset", "toast")

    def __init__(self) -> None:
        """Start following the end of the output, with nothing selected."""
        self.offset: int | None = None
        self.drag: Selection | None = None
        self.drift = 0
        self.following = True
        self.toast: Toast | None = None

    @property
    def selection(self) -> Selection | None:
        """The selection to paint, or `None` when there is nothing to show."""
        if self.drag is None or self.drag.empty:
            return None
        return self.drag

    @property
    def stamp(self) -> tuple[Any, ...]:
        """Everything about the pane that changes what is on screen.

        Compared against the last drawn frame so a turn that changed nothing draws nothing —
        the pane repaints on a scroll or a drag, not merely on a tick.
        """
        selection = self.selection
        mark = None if selection is None else (selection.anchor, selection.cursor)
        message = None if self.toast is None else self.toast.message
        return (self.offset, mark, message)

    def key(self, key: int, *, total: int, visible: int) -> None:
        """Apply one scroll key."""
        self.offset = scrolled(self.offset, key, total, visible)
        self.following = self.offset is None

    def mouse(
        self, event: Mouse, *, lines: list[str], visible: int, now: float
    ) -> None:
        """Apply one mouse report: scroll the wheel, or begin, extend or finish a drag.

        The wheel is asked about first, and has to be: on a mouse-version 1 build a wheel tick
        down and a drag's motion are the same state, and only `Mouse.wheel` knowing whether a
        drag is in progress can separate them. Asking in the other order would read every wheel
        tick as motion.
        """
        turn = event.wheel(dragging=self.drag is not None)
        if turn:
            self._scroll(turn * WHEEL_LINES, total=len(lines), visible=visible)
        elif event.pressed:
            self._begin(event, lines=lines, visible=visible)
        elif event.released:
            self._finish(event, lines=lines, visible=visible, now=now)
        elif self.drag is not None:
            self.drag.extend(self._point(event, lines=lines, visible=visible))
            self.drift = _drift(event.row, visible)

    def tick(self, now: float, *, total: int, visible: int) -> None:
        """Carry the pane forward one turn: auto-scroll, and expire a toast.

        Auto-scroll is applied here rather than when the pointer moves because a terminal only
        reports the mouse when it *changes*. Holding still just past the bottom edge produces no
        further events, and scrolling that stopped the moment the pointer settled would be the
        one moment it was most obviously wanted.
        """
        if self.drift and self.drag is not None:
            self.offset = max(
                0,
                min(
                    clamp(self.offset, total, visible) + self.drift,
                    max(0, total - visible),
                ),
            )
        if self.toast is not None and self.toast.expired(now):
            self.toast = None

    # ── internals ────────────────────────────────────────────────────

    def _scroll(self, delta: int, *, total: int, visible: int) -> None:
        """Move the view by `delta` lines, resuming following once it reaches the end.

        The same arithmetic `scrolled` applies to a key, including its one non-obvious part:
        landing on the last line returns to `None` rather than pinning there, so a reader who
        wheels back to the bottom starts following new output again instead of freezing one line
        short of it and concluding the command had stopped.
        """
        bottom = max(0, total - visible)
        settled = max(0, min(clamp(self.offset, total, visible) + delta, bottom))
        self.offset = None if settled >= bottom else settled
        self.following = self.offset is None

    def _begin(self, event: Mouse, *, lines: list[str], visible: int) -> None:
        """Pin the view and anchor a selection where the button went down.

        Pinning is the whole reason a press does anything besides start a drag: text that is
        still scrolling cannot be selected, so pressing on it says "hold still".
        """
        self.following = self.offset is None
        self.offset = clamp(self.offset, len(lines), visible)
        self.drag = Selection(self._point(event, lines=lines, visible=visible))
        self.drift = 0

    def _finish(
        self, event: Mouse, *, lines: list[str], visible: int, now: float
    ) -> None:
        """Take the selection to where the button came up, copy it, and say so.

        **The release is what defines the selection, not the motion before it.** Most terminals
        are only ever asked for presses and releases — motion reporting has to be turned on
        separately, and a terminal that ignores the request sends nothing in between. Building
        the selection out of motion alone means those terminals select nothing at all: the
        anchor is never extended, so the release copies an empty range and silently does
        nothing. Extending to the release point makes press-then-release enough on its own, and
        leaves motion doing what it should — showing the highlight as it is dragged.
        """
        drag, self.drag, self.drift = self.drag, None, 0
        if drag is None:
            return
        drag.extend(self._point(event, lines=lines, visible=visible))
        text = drag.text(lines)
        if not text:
            # A click that never became a drag. Nothing was chosen, so nothing is claimed —
            # and the view goes back to following, since pinning it was only for the drag.
            if self.following:
                self.offset = None
            return
        self.toast = Toast(COPIED if clipboard.copy(text) else UNCOPIED, at=now)

    def _point(self, event: Mouse, *, lines: list[str], visible: int) -> Point:
        """The position in the text under a screen cell.

        Clamped to what is on screen, so dragging into the header, off the bottom, or past the
        end of a short line all land somewhere real — on the first or last line *visible*,
        never on text the reader cannot see being selected. Dragging further than that is what
        auto-scroll is for: the line comes to the pointer, and the next report lands on it.

        The column may sit one past the last character, which is "selected to the end of it".
        """
        top = clamp(self.offset, len(lines), visible)
        lowest = min(top + visible - 1, max(len(lines) - 1, 0))
        index = min(max(top + event.row - HEADER, top), lowest)
        width = len(lines[index]) if lines else 0
        return (index, min(max(event.column, 0), width))


def pending(screen: Any) -> list[Event]:
    """Every event waiting right now, in order.

    Draining the buffer matters more than it looks. Taking one event per turn caps input at one
    per tick — and a held arrow key, a wheel or a dragged mouse emits them faster than that, so
    the backlog grows and the screen falls further behind the longer someone scrolls. Reading
    until the buffer is empty means the view lands where the input actually got to.
    """
    events: list[Event] = []
    while len(events) < BURST:
        key = _poll(screen)
        if key == NOTHING:
            break
        if key == curses.KEY_MOUSE:
            report = mouse_report(screen)
            if report is not None:
                events.append(report)
            continue
        events.append(key)
    return _assembled(events)


SEQUENCES: dict[tuple[int, ...], int] = {
    (91, 65): curses.KEY_UP,
    (91, 66): curses.KEY_DOWN,
    (91, 67): curses.KEY_RIGHT,
    (91, 68): curses.KEY_LEFT,
    (91, 70): curses.KEY_END,
    (91, 72): curses.KEY_HOME,
    (79, 65): curses.KEY_UP,
    (79, 66): curses.KEY_DOWN,
    (79, 67): curses.KEY_RIGHT,
    (79, 68): curses.KEY_LEFT,
    (79, 70): curses.KEY_END,
    (79, 72): curses.KEY_HOME,
    (91, 49, 126): curses.KEY_HOME,
    (91, 52, 126): curses.KEY_END,
    (91, 53, 126): curses.KEY_PPAGE,
    (91, 54, 126): curses.KEY_NPAGE,
    (91, 55, 126): curses.KEY_HOME,
    (91, 56, 126): curses.KEY_END,
}
"""What an arrow or a paging key looks like when it arrives in pieces.

**ncurses does not reliably assemble these while the window is in `nodelay`.** It is documented
as returning what is available rather than waiting for the rest of a sequence, and in a live
pane an arrow key turns up as `ESC`, `[`, `A` — three separate reads — often enough to matter.
Mouse reports survive because ncurses reads their payload itself rather than through the key
table, which is exactly why a drag works in a pane where the arrow keys did not.

Two things went wrong because of it, and both look like something else entirely: scrolling a
running command with the arrow keys did nothing, and — worse — the leading `ESC` reads as
"cancel", so reaching for `→` to answer *Stop this?* dismissed the question instead.

Both cursor forms are here. A terminal switched to application mode sends `ESC O A` for the
same key that is `ESC [ A` in normal mode, and curses may be in either.
"""

LONGEST = 3
"""The most bytes after the `ESC` that any of the above takes."""


def _assembled(events: list[Event]) -> list[Event]:
    """Put split escape sequences back together, leaving everything else alone.

    A bare `ESC` still means `ESC`: bytes are only consumed when what follows them spells a key
    this knows. That leaves the ambiguity every terminal has — an `ESC` typed a moment before an
    arrow key is indistinguishable from the arrow key alone — and resolves it the same way, in
    favour of the sequence, because that is overwhelmingly what it is.
    """
    assembled: list[Event] = []
    index = 0
    while index < len(events):
        key = _sequence(events, index)
        if key is None:
            assembled.append(events[index])
            index += 1
            continue
        code, length = key
        assembled.append(code)
        index += 1 + length
    return assembled


def _sequence(events: list[Event], index: int) -> tuple[int, int] | None:
    """The key spelled at `index`, and how many bytes follow the `ESC`, or `None`."""
    if events[index] != ESC:
        return None
    for length in range(LONGEST, 0, -1):
        tail = events[index + 1 : index + 1 + length]
        if len(tail) == length and all(isinstance(part, int) for part in tail):
            code = SEQUENCES.get(tuple(tail))  # type: ignore[arg-type]
            if code is not None:
                return (code, length)
    return None


NOTHING = -1
"""What a non-blocking `getch` returns when no key is waiting."""

BURST = 128
"""Events taken in one turn before the loop yields again.

A ceiling rather than a target: whatever is left stays buffered for the next turn, so a terminal
pasting a wall of input cannot starve the command of the event loop.
"""


def _poll(screen: Any) -> int:
    """Read a key if one is waiting, or `NOTHING`.

    Ctrl+C is still caught defensively: raw mode should deliver it as a byte, but a terminal
    that disagrees would otherwise unwind the whole menu from inside a draw.
    """
    try:
        return int(screen.getch())
    except KeyboardInterrupt:
        return INTERRUPT


def _drift(row: int, visible: int) -> int:
    """How far to scroll per turn because a drag is held past an edge.

    Proportional to the overshoot rather than a fixed step: a pointer just outside the pane
    creeps, one dragged well past it moves. That is the difference between auto-scroll that
    feels aimed and one that feels like a switch.
    """
    if row < HEADER:
        return max(row - HEADER, -FASTEST)
    if row > visible:
        return min(row - visible, FASTEST)
    return 0
