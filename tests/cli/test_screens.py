"""The screens, driven by a scripted keyboard against a fake window."""

import click
import pytest

from dexter.cli.interactive import Menu
from dexter.cli.interactive.screens import (
    confirm_screen,
    list_screen,
    output_screen,
    params_screen,
)
from dexter.cli.models import read_fields

from .conftest import choose, count, greet
from .screen import FakeScreen, OutOfKeysError, fake_curs_set


def make_tree() -> click.Group:
    """A root with one command and one group of two."""
    root = click.Group(name=None)
    root.add_command(greet)
    numbers = click.Group(name="numbers", help="Number things.")
    numbers.add_command(count)
    numbers.add_command(choose)
    root.add_command(numbers)
    return root


class TestListScreen:
    def test_draws_every_command_with_its_description(self) -> None:
        screen = FakeScreen(["enter"])
        list_screen(screen, Menu(make_tree(), "test"))

        drawn = screen.last_text()
        assert "greet" in drawn
        assert "Say hello." in drawn
        assert "numbers" in drawn

    def test_marks_a_group_as_opening_a_submenu(self) -> None:
        screen = FakeScreen(["enter"])
        list_screen(screen, Menu(make_tree(), "test"))

        assert "\u203a" in screen.row_containing("numbers")

    def test_shows_the_breadcrumb(self) -> None:
        screen = FakeScreen(["enter"])
        list_screen(screen, Menu(make_tree(), "a title"))

        assert "a title" in screen.last[0]

    def test_enter_selects(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["enter"])

        assert list_screen(screen, menu) is True
        assert menu.cursor == 0

    def test_arrows_move_the_cursor(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["down", "enter"])

        list_screen(screen, menu)

        assert menu.cursor == 1

    def test_up_moves_back(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["down", "up", "enter"])

        list_screen(screen, menu)

        assert menu.cursor == 0

    def test_page_keys_move_by_a_screen(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["pgdn", "enter"])

        list_screen(screen, menu)

        assert menu.cursor == len(menu.items()) - 1

        screen = FakeScreen(["pgup", "enter"])
        list_screen(screen, menu)
        assert menu.cursor == 0

    def test_escape_inside_a_group_goes_back_without_leaving(self) -> None:
        """One ESC is one level. Returning here would close the menu instead."""
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()
        screen = FakeScreen(["esc", "enter"])

        assert list_screen(screen, menu) is True, "going back should not end the screen"
        assert menu.at_root is True

    def test_escaping_all_the_way_out_still_leaves(self) -> None:
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()
        screen = FakeScreen(["esc", "esc", "esc"])

        assert list_screen(screen, menu) is False

    def test_one_escape_at_the_root_only_warns(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["esc", "enter"])

        assert list_screen(screen, menu) is True
        assert "ESC again" in screen.text()

    def test_two_escapes_at_the_root_leave(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["esc", "esc"])

        assert list_screen(screen, menu) is False

    def test_a_key_between_escapes_clears_the_warning(self) -> None:
        menu = Menu(make_tree(), "test")
        screen = FakeScreen(["esc", "down", "esc", "enter"])

        assert list_screen(screen, menu) is True

    def test_an_empty_group_says_so_and_ignores_enter(self) -> None:
        root = click.Group(name=None)
        root.add_command(click.Group(name="empty"))
        menu = Menu(root, "test")
        menu.enter()
        screen = FakeScreen(["enter", "esc", "esc", "esc"])

        assert list_screen(screen, menu) is False
        assert "nothing registered" in screen.text()

    def test_an_interrupt_redraws_rather_than_selecting(self) -> None:
        menu = Menu(make_tree(), "test")
        # Ctrl+C opens the modal; "stay" returns to the list, which must redraw and read on.
        screen = FakeScreen(["ctrl-c", "esc", "enter"])

        assert list_screen(screen, menu) is True

    def test_scrolls_so_the_cursor_stays_visible(self) -> None:
        root = click.Group(name=None)
        for index in range(30):
            root.add_command(click.Command(name=f"c{index:02d}", help="A command."))
        menu = Menu(root, "test")
        # A page is `visible` rows, so enough pages to reach the end of thirty commands.
        screen = FakeScreen(["pgdn"] * 5 + ["enter"], size=(10, 80))

        list_screen(screen, menu)

        assert menu.cursor == 29
        assert "c29" in screen.last_text(), "the cursor scrolled off the bottom"


class TestParamsScreen:
    def test_returns_the_defaults_when_run_is_chosen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_curs_set(monkeypatch)
        fields = read_fields(greet)
        screen = FakeScreen(["enter"])

        assert params_screen(screen, "greet", fields) == {
            "name": "world",
            "shout": "false",
        }

    def test_escape_cancels(self) -> None:
        screen = FakeScreen(["esc"])

        assert params_screen(screen, "greet", read_fields(greet)) is None

    def test_draws_a_row_for_every_field(self) -> None:
        screen = FakeScreen(["enter"])
        params_screen(screen, "greet", read_fields(greet))

        drawn = screen.last_text()
        assert "Name" in drawn
        assert "Shout" in drawn
        assert "Run greet" in drawn

    def test_shows_a_field_s_help(self) -> None:
        screen = FakeScreen(["enter"])
        params_screen(screen, "greet", read_fields(greet))

        assert "Who to greet." in screen.last_text()

    def test_enter_on_a_flag_toggles_it(self) -> None:
        screen = FakeScreen(["down", "down", "enter", "up", "up", "enter"])

        values = params_screen(screen, "greet", read_fields(greet))

        assert values is not None
        assert values["shout"] == "true"

    def test_enter_on_a_choice_cycles_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cycling means an invalid choice cannot be entered at all."""
        fake_curs_set(monkeypatch)
        screen = FakeScreen(["down", "enter", "esc"])

        params_screen(screen, "choose", read_fields(choose))

        assert "Mode: slow" in screen.last_text()

    def test_cycling_a_choice_wraps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_curs_set(monkeypatch)
        screen = FakeScreen(["down", "enter", "enter", "esc"])

        params_screen(screen, "choose", read_fields(choose))

        assert "Mode: fast" in screen.last_text()

    def test_a_required_field_blocks_the_run(self) -> None:
        screen = FakeScreen(["enter", "esc"])

        params_screen(screen, "choose", read_fields(choose))

        assert "Still needed" in screen.text()
        assert "fill in" in screen.text()

    def test_typing_into_a_field_fills_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_curs_set(monkeypatch)
        fields = read_fields(choose)
        keys: list[str | int] = ["down", "down", "enter"]
        keys += [ord(character) for character in "yes"]
        keys += ["enter", "up", "up", "enter"]
        screen = FakeScreen(keys)

        values = params_screen(screen, "choose", fields)

        assert values is not None
        assert values["needed"] == "yes"

    def test_backspace_deletes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_curs_set(monkeypatch)
        keys: list[str | int] = ["down", "down", "enter"]
        keys += [ord("a"), ord("b"), "backspace", ord("c")]
        keys += ["enter", "up", "up", "enter"]
        screen = FakeScreen(keys)

        values = params_screen(screen, "choose", read_fields(choose))

        assert values is not None
        assert values["needed"] == "ac"

    def test_escaping_the_editor_keeps_the_old_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_curs_set(monkeypatch)
        keys: list[str | int] = ["down", "down", "enter", ord("x"), "esc"]
        keys += ["up", "up", "enter", "esc"]
        screen = FakeScreen(keys)

        params_screen(screen, "choose", read_fields(choose))

        assert "Still needed" in screen.text(), "the cancelled edit was kept"

    def test_the_cursor_stops_at_the_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_curs_set(monkeypatch)
        screen = FakeScreen(["up", "up", "enter"])

        assert params_screen(screen, "greet", read_fields(greet)) is not None

    def test_an_interrupt_redraws(self) -> None:
        screen = FakeScreen(["ctrl-c", "esc", "enter"])

        assert params_screen(screen, "greet", read_fields(greet)) is not None


class TestConfirmScreen:
    def test_shows_the_shell_command(self) -> None:
        screen = FakeScreen(["enter"])

        assert confirm_screen(screen, "./dx greet --shout") is True
        assert "./dx greet --shout" in screen.last_text()
        assert "This will run" in screen.last_text()

    def test_escape_cancels(self) -> None:
        screen = FakeScreen(["esc"])

        assert confirm_screen(screen, "./dx greet") is False

    def test_an_unrelated_key_waits(self) -> None:
        screen = FakeScreen([ord("x"), "enter"])

        assert confirm_screen(screen, "./dx greet") is True

    def test_an_interrupt_redraws(self) -> None:
        screen = FakeScreen(["ctrl-c", "esc", "enter"])

        assert confirm_screen(screen, "./dx greet") is True


class TestOutputScreen:
    def test_draws_the_output(self) -> None:
        screen = FakeScreen(["enter"])
        output_screen(screen, "greet", "hello\nworld")

        assert "hello" in screen.last_text()
        assert "world" in screen.last_text()

    def test_says_so_when_there_is_no_output(self) -> None:
        screen = FakeScreen(["enter"])
        output_screen(screen, "greet", "")

        assert "(no output)" in screen.last_text()

    def test_live_mode_returns_without_waiting_for_a_key(self) -> None:
        screen = FakeScreen([])
        output_screen(screen, "greet", "working", live=True)

        assert "running" in screen.last_text()
        assert screen.reads == 0

    def test_live_mode_pins_the_view_to_the_end(self) -> None:
        screen = FakeScreen([], size=(6, 80))
        output_screen(screen, "greet", "\n".join(str(n) for n in range(20)), live=True)

        assert "19" in screen.last_text()
        assert "0\n" not in screen.last_text()

    def test_scrolling_moves_the_view(self) -> None:
        text = "\n".join(str(number) for number in range(50))
        screen = FakeScreen(["down", "down", "enter"], size=(8, 80))

        output_screen(screen, "greet", text)

        assert "2" in screen.last_text()

    def test_page_keys_scroll_by_a_screen(self) -> None:
        text = "\n".join(str(number) for number in range(50))
        screen = FakeScreen(["pgdn", "pgup", "up", "enter"], size=(8, 80))

        output_screen(screen, "greet", text)

        assert screen.frames

    def test_any_other_key_returns(self) -> None:
        screen = FakeScreen([ord("q")])

        output_screen(screen, "greet", "hello")

        assert screen.reads == 1

    def test_an_interrupt_redraws(self) -> None:
        screen = FakeScreen(["ctrl-c", "esc", ord("q")])

        output_screen(screen, "greet", "hello")


class TestFakeScreenItself:
    def test_running_out_of_keys_is_loud(self) -> None:
        """A screen that keeps reading did not return when it should have."""
        screen = FakeScreen([])

        with pytest.raises(OutOfKeysError):
            confirm_screen(screen, "./dx greet")
