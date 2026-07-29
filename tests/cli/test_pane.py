"""The output pane: scrolling, dragging a selection across it, and copying what was dragged."""

import curses
from typing import Any

import pytest

from dexter.cli import clipboard
from dexter.cli.interactive.pane import (
    BURST,
    COPIED,
    FASTEST,
    UNCOPIED,
    Mouse,
    Pane,
    pending,
)

from .screen import FakeScreen, drag, press, release

LINES = [f"line {index}" for index in range(20)]
VISIBLE = 10


@pytest.fixture
def copied(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record what reached the clipboard instead of putting it there."""
    taken: list[str] = []

    def fake_copy(text: str) -> bool:
        taken.append(text)
        return True

    monkeypatch.setattr(clipboard, "copy", fake_copy)
    return taken


def _mouse(row: int, column: int, state: int) -> Mouse:
    return Mouse(row=row, column=column, state=state)


def _press(pane: Pane, row: int, column: int, *, now: float = 0.0) -> None:
    pane.mouse(
        _mouse(row, column, curses.BUTTON1_PRESSED),
        lines=LINES,
        visible=VISIBLE,
        now=now,
    )


def _drag(pane: Pane, row: int, column: int, *, now: float = 0.0) -> None:
    pane.mouse(
        _mouse(row, column, curses.REPORT_MOUSE_POSITION),
        lines=LINES,
        visible=VISIBLE,
        now=now,
    )


def _release(pane: Pane, row: int, column: int, *, now: float = 0.0) -> None:
    pane.mouse(
        _mouse(row, column, curses.BUTTON1_RELEASED),
        lines=LINES,
        visible=VISIBLE,
        now=now,
    )


class TestScrolling:
    def test_a_new_pane_follows_the_end(self) -> None:
        assert Pane().offset is None

    def test_a_scroll_key_pins_the_view(self) -> None:
        pane = Pane()
        pane.key(curses.KEY_UP, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 9
        assert not pane.following

    def test_scrolling_back_to_the_bottom_resumes_following(self) -> None:
        pane = Pane()
        pane.key(curses.KEY_HOME, total=len(LINES), visible=VISIBLE)
        pane.key(curses.KEY_END, total=len(LINES), visible=VISIBLE)

        assert pane.offset is None
        assert pane.following


class TestDragging:
    def test_pressing_pins_the_view_where_it_was(self) -> None:
        """Text that is still scrolling cannot be selected, so a press stops it."""
        pane = Pane()
        _press(pane, 1, 0)

        assert pane.offset == 10

    def test_a_press_alone_selects_nothing(self) -> None:
        pane = Pane()
        _press(pane, 1, 0)

        assert pane.selection is None

    def test_a_drag_selects_from_where_it_started(self) -> None:
        pane = Pane()
        _press(pane, 1, 2)
        _drag(pane, 3, 4)

        selection = pane.selection
        assert selection is not None
        assert (selection.start, selection.end) == ((10, 2), (12, 4))

    def test_the_selection_is_anchored_to_the_text_not_the_screen(self) -> None:
        """Scrolling mid-drag moves the view, not the words that were dragged across."""
        pane = Pane()
        _press(pane, 5, 0)
        pane.offset = 4
        _drag(pane, 5, 3)

        selection = pane.selection
        assert selection is not None
        assert selection.anchor == (14, 0)
        assert selection.cursor == (8, 3)

    def test_a_drag_into_the_header_lands_on_the_top_line(self) -> None:
        pane = Pane()
        _press(pane, 5, 0)
        _drag(pane, 0, 2)

        selection = pane.selection
        assert selection is not None
        assert selection.cursor == (10, 2)

    def test_a_drag_past_the_end_of_a_line_stops_at_its_end(self) -> None:
        pane = Pane()
        _press(pane, 1, 0)
        _drag(pane, 1, 400)

        selection = pane.selection
        assert selection is not None
        assert selection.cursor == (10, len("line 10"))

    def test_a_mouse_report_before_any_press_is_ignored(self) -> None:
        pane = Pane()
        _drag(pane, 4, 4)

        assert pane.selection is None


class TestAutoScroll:
    def test_holding_past_the_bottom_scrolls_down(self) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 1, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 1

    def test_holding_further_past_it_scrolls_faster(self) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 2, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 2

    def test_it_never_scrolls_faster_than_the_cap(self) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 99, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == FASTEST

    def test_holding_above_the_top_scrolls_up(self) -> None:
        pane = Pane()
        pane.offset = 5
        _press(pane, 1, 0)
        _drag(pane, 0, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 4

    def test_it_keeps_scrolling_while_the_pointer_holds_still(self) -> None:
        """A terminal only reports the mouse when it moves; the scroll must not stop with it."""
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 1, 0)
        for _ in range(3):
            pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 3

    def test_it_stops_at_the_end_of_the_output(self) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 99, 0)
        for _ in range(20):
            pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == len(LINES) - VISIBLE

    def test_it_stops_when_the_button_comes_up(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, VISIBLE + 1, 0)
        _release(pane, VISIBLE + 1, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 0

    def test_a_drag_inside_the_pane_does_not_scroll(self) -> None:
        pane = Pane()
        pane.offset = 5
        _press(pane, 1, 0)
        _drag(pane, 4, 0)
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.offset == 5


class TestCopying:
    def test_a_press_and_a_release_are_enough_on_their_own(
        self, copied: list[str]
    ) -> None:
        """No motion in between, which is all most terminals ever report.

        ncurses asks for mouse reporting from the `XM` terminfo capability and falls back to
        presses and releases only when it is missing — `xterm-256color` included. A selection
        built out of motion events alone is one those terminals can never make: the anchor is
        never extended, so the release copies an empty range and appears to do nothing.
        """
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _release(pane, 3, 4)

        assert copied == ["line 0\nline 1\nline"]

    def test_the_release_wins_over_the_last_motion(self, copied: list[str]) -> None:
        """The pointer can move further between the last report and the button coming up."""
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        _release(pane, 2, 6)

        assert copied == ["line 0\nline 1"]

    def test_releasing_copies_what_was_dragged_across(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 2, 6)
        _release(pane, 2, 6)

        assert copied == ["line 0\nline 1"]

    def test_it_says_so(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        _release(pane, 1, 4, now=100.0)

        assert pane.toast is not None
        assert pane.toast.message == COPIED

    def test_it_admits_when_the_clipboard_could_not_be_reached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(clipboard, "copy", lambda text: False)
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        _release(pane, 1, 4)

        assert pane.toast is not None
        assert pane.toast.message == UNCOPIED

    def test_the_toast_goes_away_by_itself(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        _release(pane, 1, 4, now=100.0)
        pane.tick(120.0, total=len(LINES), visible=VISIBLE)

        assert pane.toast is None

    def test_the_selection_is_cleared_on_release(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        _release(pane, 1, 4)

        assert pane.selection is None

    def test_a_click_that_never_became_a_drag_copies_nothing(
        self, copied: list[str]
    ) -> None:
        pane = Pane()
        _press(pane, 1, 0)
        _release(pane, 1, 0)

        assert copied == []
        assert pane.toast is None

    def test_a_click_leaves_the_pane_following_the_end(self, copied: list[str]) -> None:
        """Pinning the view was for the drag's sake, and there was no drag."""
        pane = Pane()
        _press(pane, 1, 0)
        _release(pane, 1, 0)

        assert pane.offset is None

    def test_a_click_after_scrolling_leaves_the_view_where_it_was(
        self, copied: list[str]
    ) -> None:
        pane = Pane()
        pane.key(curses.KEY_HOME, total=len(LINES), visible=VISIBLE)
        _press(pane, 1, 0)
        _release(pane, 1, 0)

        assert pane.offset == 0

    def test_a_release_with_no_press_does_nothing(self, copied: list[str]) -> None:
        pane = Pane()
        _release(pane, 1, 0)

        assert copied == []
        assert pane.toast is None


class TestStamp:
    def test_it_changes_when_the_view_moves(self) -> None:
        pane = Pane()
        before = pane.stamp
        pane.key(curses.KEY_UP, total=len(LINES), visible=VISIBLE)

        assert pane.stamp != before

    def test_it_changes_when_the_selection_grows(self) -> None:
        pane = Pane()
        _press(pane, 1, 0)
        before = pane.stamp
        _drag(pane, 1, 4)

        assert pane.stamp != before

    def test_it_changes_when_a_toast_appears(self, copied: list[str]) -> None:
        pane = Pane()
        pane.offset = 0
        _press(pane, 1, 0)
        _drag(pane, 1, 4)
        before = pane.stamp
        _release(pane, 1, 4)

        assert pane.stamp != before

    def test_a_turn_that_changed_nothing_leaves_it_alone(self) -> None:
        pane = Pane()
        before = pane.stamp
        pane.tick(0.0, total=len(LINES), visible=VISIBLE)

        assert pane.stamp == before


class TestPending:
    def test_it_drains_everything_waiting(self) -> None:
        screen = FakeScreen(["up", "down", "up"])
        screen.nodelay(True)
        screen.keys_per_turn = 3

        assert pending(screen) == [curses.KEY_UP, curses.KEY_DOWN, curses.KEY_UP]

    def test_an_empty_buffer_gives_nothing(self) -> None:
        screen = FakeScreen()
        screen.nodelay(True)

        assert pending(screen) == []

    def test_it_decodes_a_mouse_report(self) -> None:
        screen = FakeScreen([press(3, 7)])
        screen.nodelay(True)

        assert pending(screen) == [Mouse(row=3, column=7, state=curses.BUTTON1_PRESSED)]

    def test_a_report_it_cannot_read_is_dropped(self) -> None:
        """`KEY_MOUSE` with nothing behind it: rare, uninteresting, and not fatal."""
        screen = FakeScreen([curses.KEY_MOUSE, "up"])
        screen.nodelay(True)
        screen.keys_per_turn = 2

        assert pending(screen) == [curses.KEY_UP]

    def test_it_takes_no_more_than_a_burst_at_a_time(self) -> None:
        screen = FakeScreen(["up"] * (BURST + 10))
        screen.nodelay(True)
        screen.keys_per_turn = BURST + 10

        assert len(pending(screen)) == BURST

    def test_a_ctrl_c_that_arrives_as_an_exception_is_still_a_key(self) -> None:
        """Raw mode should deliver it as a byte; a terminal that disagrees is caught anyway."""
        assert pending(_Interrupting()) == [3]


class _Interrupting:
    """A window whose first read raises `KeyboardInterrupt` and whose next is empty."""

    def __init__(self) -> None:
        self.raised = False

    def getch(self) -> int:
        if self.raised:
            return -1
        self.raised = True
        raise KeyboardInterrupt


class TestSplitEscapeSequences:
    """ncurses hands these over in pieces while the window is polling.

    Measured, not assumed: an arrow key pressed over a running command arrives as `27, 91, 65`
    and never reaches `SCROLL_KEYS`, so the pane does not move. The same leading `27` reaching
    the modal reads as ESC, which answers *Stop this?* with "keep running" — using the arrow
    key meant to reach `Stop` cancelled the question.
    """

    def _drained(self, *keys: int) -> list[Any]:
        screen = FakeScreen(list(keys))
        screen.nodelay(True)
        screen.keys_per_turn = len(keys)
        return pending(screen)

    def test_an_arrow_is_put_back_together(self) -> None:
        assert self._drained(27, 91, 65) == [curses.KEY_UP]

    def test_every_direction(self) -> None:
        assert self._drained(27, 91, 66) == [curses.KEY_DOWN]
        assert self._drained(27, 91, 67) == [curses.KEY_RIGHT]
        assert self._drained(27, 91, 68) == [curses.KEY_LEFT]

    def test_application_cursor_mode_too(self) -> None:
        """The same key is `ESC O A` when the terminal has been switched over."""
        assert self._drained(27, 79, 65) == [curses.KEY_UP]

    def test_a_paging_key(self) -> None:
        assert self._drained(27, 91, 53, 126) == [curses.KEY_PPAGE]
        assert self._drained(27, 91, 54, 126) == [curses.KEY_NPAGE]

    def test_home_and_end(self) -> None:
        assert self._drained(27, 91, 72) == [curses.KEY_HOME]
        assert self._drained(27, 91, 70) == [curses.KEY_END]

    def test_a_bare_escape_is_still_an_escape(self) -> None:
        assert self._drained(27) == [27]

    def test_an_escape_before_something_unknown_is_left_alone(self) -> None:
        assert self._drained(27, 91, 90) == [27, 91, 90]

    def test_one_already_assembled_is_untouched(self) -> None:
        assert self._drained(curses.KEY_PPAGE) == [curses.KEY_PPAGE]

    def test_several_in_one_turn(self) -> None:
        assert self._drained(27, 91, 65, 27, 91, 66, 3) == [
            curses.KEY_UP,
            curses.KEY_DOWN,
            3,
        ]

    def test_ordinary_keys_are_carried_through(self) -> None:
        assert self._drained(ord("q"), 27, 91, 65, ord("x")) == [
            ord("q"),
            curses.KEY_UP,
            ord("x"),
        ]

    def test_a_mouse_report_between_two_of_them_survives(self) -> None:
        screen = FakeScreen([27, 91, 65, press(2, 2), 27, 91, 66])
        screen.nodelay(True)
        screen.keys_per_turn = 7
        events = pending(screen)

        assert events[0] == curses.KEY_UP
        assert isinstance(events[1], Mouse)
        assert events[2] == curses.KEY_DOWN


class TestMouseReport:
    def test_a_press_is_a_press(self) -> None:
        assert Mouse(1, 1, curses.BUTTON1_PRESSED).pressed

    def test_a_release_is_a_release(self) -> None:
        assert Mouse(1, 1, curses.BUTTON1_RELEASED).released

    def test_motion_is_neither(self) -> None:
        motion = Mouse(1, 1, curses.REPORT_MOUSE_POSITION)

        assert not motion.pressed
        assert not motion.released


class TestScriptedReports:
    def test_a_scripted_drag_arrives_as_three_reports(self) -> None:
        screen = FakeScreen([press(2, 0), drag(4, 5), release(4, 5)])
        screen.nodelay(True)
        screen.keys_per_turn = 3

        events = [event for event in pending(screen) if isinstance(event, Mouse)]
        assert [(event.pressed, event.released) for event in events] == [
            (True, False),
            (False, False),
            (False, True),
        ]
