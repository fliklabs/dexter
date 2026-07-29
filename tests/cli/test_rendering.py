"""Drawing primitives, key reading, and putting the terminal back the way it was found."""

import curses
import os
from typing import Any

import pytest

from dexter.cli.interactive import rendering
from dexter.cli.interactive.rendering import (
    REDRAW,
    Quit,
    body_height,
    confirm_quit,
    footer,
    header,
    read_key,
    show_cursor,
    terminal,
    write,
)

from .screen import FakeScreen


class TestWrite:
    def test_draws_at_the_given_position(self) -> None:
        screen = FakeScreen()
        write(screen, 1, 2, "hello")
        screen.refresh()

        assert screen.last[1] == "  hello"

    def test_clips_to_the_window_width(self) -> None:
        """curses raises if anything reaches the last cell, so nothing may."""
        screen = FakeScreen(size=(24, 20))
        write(screen, 0, 0, "x" * 100)
        screen.refresh()

        assert len(screen.last[0]) <= 19

    def test_ignores_a_row_below_the_window(self) -> None:
        screen = FakeScreen(size=(5, 20))
        write(screen, 99, 0, "nope")
        screen.refresh()

        assert "nope" not in screen.last_text()

    def test_ignores_a_negative_row(self) -> None:
        screen = FakeScreen()
        write(screen, -1, 0, "nope")
        screen.refresh()

        assert "nope" not in screen.last_text()

    def test_ignores_a_column_past_the_edge(self) -> None:
        screen = FakeScreen(size=(24, 10))
        write(screen, 0, 50, "nope")
        screen.refresh()

        assert "nope" not in screen.last_text()

    def test_survives_a_curses_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A window smaller than curses admits to must not take the menu down."""

        class Hostile(FakeScreen):
            def addstr(self, *args: Any, **kwargs: Any) -> None:
                raise curses.error("no room")

        write(Hostile(), 0, 0, "hello")


class TestHeaderAndFooter:
    def test_the_header_fills_the_width(self) -> None:
        screen = FakeScreen(size=(24, 30))
        header(screen, "title")
        screen.refresh()

        assert screen.last[0].startswith("title")

    def test_the_footer_is_on_the_last_line(self) -> None:
        screen = FakeScreen(size=(10, 30))
        footer(screen, "hints")
        screen.refresh()

        assert "hints" in screen.last[9]

    def test_the_body_is_what_is_left(self) -> None:
        assert body_height(FakeScreen(size=(24, 80))) == 22

    def test_the_body_is_never_zero(self) -> None:
        """A one-line window still has to draw something rather than divide by nothing."""
        assert body_height(FakeScreen(size=(1, 80))) == 1


class TestReadKey:
    def test_returns_an_ordinary_key(self) -> None:
        assert read_key(FakeScreen([ord("x")])) == ord("x")

    def test_an_interrupt_that_is_cancelled_asks_for_a_redraw(self) -> None:
        screen = FakeScreen(["ctrl-c", "esc"])

        assert read_key(screen) == REDRAW

    def test_an_interrupt_that_is_confirmed_quits(self) -> None:
        screen = FakeScreen(["ctrl-c", "right", "enter"])

        with pytest.raises(Quit):
            read_key(screen)

    def test_a_keyboard_interrupt_is_treated_as_ctrl_c(self) -> None:
        """`curses.raw()` should prevent this, but a stray one must not escape."""

        class Interrupting(FakeScreen):
            def getch(self) -> int:
                if self.reads == 0:
                    self.reads += 1
                    raise KeyboardInterrupt
                return super().getch()

        assert read_key(Interrupting(["esc"])) == REDRAW


class TestConfirmQuit:
    def test_defaults_to_staying(self) -> None:
        assert confirm_quit(FakeScreen(["enter"])) is False

    def test_escape_stays(self) -> None:
        assert confirm_quit(FakeScreen(["esc"])) is False

    def test_choosing_quit_quits(self) -> None:
        assert confirm_quit(FakeScreen(["right", "enter"])) is True

    def test_tab_moves_between_the_buttons(self) -> None:
        assert confirm_quit(FakeScreen(["tab", "enter"])) is True

    def test_left_moves_back(self) -> None:
        assert confirm_quit(FakeScreen(["right", "left", "enter"])) is False

    def test_a_second_interrupt_confirms(self) -> None:
        assert confirm_quit(FakeScreen(["ctrl-c"])) is True

    def test_a_keyboard_interrupt_confirms(self) -> None:
        class Interrupting(FakeScreen):
            def getch(self) -> int:
                raise KeyboardInterrupt

        assert confirm_quit(Interrupting([])) is True

    def test_it_says_what_it_is_asking(self) -> None:
        screen = FakeScreen(["esc"])
        confirm_quit(screen)

        assert "Leave the menu?" in screen.text()


class TestShowCursor:
    def test_a_terminal_that_cannot_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(visibility: int) -> int:
            raise curses.error("no cursor here")

        monkeypatch.setattr(curses, "curs_set", refuse)

        show_cursor(visible=True)
        show_cursor(visible=False)


class TestTerminal:
    def install(self, monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> FakeScreen:
        """Replace every curses call `terminal` makes with a recorder."""
        screen = FakeScreen()

        def record(name: str) -> Any:
            def recorder(*args: Any, **kwargs: Any) -> Any:
                calls.append(name)
                return screen if name == "initscr" else None

            return recorder

        for name in (
            "initscr",
            "noecho",
            "cbreak",
            "raw",
            "curs_set",
            "start_color",
            "use_default_colors",
            "noraw",
            "nocbreak",
            "echo",
            "endwin",
        ):
            monkeypatch.setattr(curses, name, record(name))
        return screen

    def test_sets_up_and_tears_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        expected = self.install(monkeypatch, calls)

        with terminal() as screen:
            assert screen is expected
            calls.append("body")

        assert calls.index("initscr") < calls.index("body")
        assert calls.index("body") < calls.index("endwin")
        assert "raw" in calls
        assert "noraw" in calls

    def test_restores_the_terminal_when_the_body_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the user is left with a shell they have to `reset` by hand."""
        calls: list[str] = []
        self.install(monkeypatch, calls)

        with pytest.raises(RuntimeError), terminal():
            raise RuntimeError("boom")

        assert calls[-1] == "endwin"
        assert "echo" in calls

    def test_colour_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A terminal without colour still gets a menu."""
        calls: list[str] = []
        self.install(monkeypatch, calls)

        def refuse() -> None:
            raise curses.error("no colour")

        monkeypatch.setattr(curses, "start_color", refuse)

        with terminal():
            pass

        assert calls[-1] == "endwin"


class TestEscapeDelay:
    def test_is_set_before_curses_starts(self) -> None:
        """Left at its default, a bare ESC takes about a second to register."""
        assert os.environ["ESCDELAY"] == "25"
        assert rendering.ESC == 27
