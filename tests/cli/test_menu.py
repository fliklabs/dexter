"""The menu's decisions, tested without a terminal.

`Menu` holds every choice the interactive layer makes — where the cursor is, what going back
means, what path a selection produces — and touches no curses at all. That separation is what
makes this file possible; only the drawing is left uncovered.
"""

import click

from dexter.cli import read_fields, shell_command
from dexter.cli.interactive import Menu
from dexter.cli.models import FieldKind, missing_required, to_argv

from .conftest import choose, count, greet


def make_tree() -> click.Group:
    """A root with one loose command and one group of two."""
    root = click.Group(name=None)
    root.add_command(greet)
    numbers = click.Group(name="numbers", help="Number things.")
    numbers.add_command(count)
    numbers.add_command(choose)
    root.add_command(numbers)
    return root


class TestMoving:
    def test_starts_at_the_top_of_the_root(self) -> None:
        menu = Menu(make_tree(), "test")

        assert menu.cursor == 0
        assert menu.at_root is True

    def test_moves_down_and_up(self) -> None:
        menu = Menu(make_tree(), "test")

        menu.move(1)
        assert menu.cursor == 1
        menu.move(-1)
        assert menu.cursor == 0

    def test_stops_at_the_ends_rather_than_wrapping(self) -> None:
        menu = Menu(make_tree(), "test")

        menu.move(-5)
        assert menu.cursor == 0
        menu.move(50)
        assert menu.cursor == len(menu.items()) - 1

    def test_lists_commands_alphabetically(self) -> None:
        menu = Menu(make_tree(), "test")

        assert [name for name, _ in menu.items()] == ["greet", "numbers"]


class TestNesting:
    def test_entering_a_group_opens_it(self) -> None:
        menu = Menu(make_tree(), "test")
        menu.move(1)

        assert menu.enter() is None, "a group is opened rather than returned"
        assert menu.at_root is False
        assert [name for name, _ in menu.items()] == ["choose", "count"]

    def test_entering_a_command_returns_it(self) -> None:
        menu = Menu(make_tree(), "test")

        selected = menu.enter()

        assert selected is greet

    def test_going_back_closes_the_group(self) -> None:
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()

        assert menu.back() is True
        assert menu.at_root is True

    def test_going_back_at_the_root_does_nothing(self) -> None:
        menu = Menu(make_tree(), "test")

        assert menu.back() is False

    def test_each_level_remembers_where_the_cursor_was(self) -> None:
        """Backing out of a submenu must return to the row you left, not to the top."""
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()
        menu.move(1)
        assert menu.cursor == 1

        menu.back()
        assert menu.cursor == 1, "the root's cursor was reset"

    def test_a_reopened_group_starts_at_the_top(self) -> None:
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()
        menu.move(1)
        menu.back()
        menu.enter()

        assert menu.cursor == 0


class TestDescribing:
    def test_the_breadcrumb_grows_with_the_path(self) -> None:
        menu = Menu(make_tree(), "test")
        assert menu.breadcrumb() == "test"

        menu.move(1)
        menu.enter()
        assert "numbers" in menu.breadcrumb()

    def test_rows_carry_the_docstring_and_whether_they_open(self) -> None:
        menu = Menu(make_tree(), "test")
        rows = {name: (description, opens) for name, description, opens in menu.rows()}

        assert rows["greet"] == ("Say hello.", False)
        assert rows["numbers"][1] is True

    def test_the_command_path_is_what_a_user_would_type(self) -> None:
        menu = Menu(make_tree(), "test")
        menu.move(1)
        menu.enter()
        selected = menu.enter()

        assert selected is not None
        assert menu.command_path(selected) == ("numbers", "choose")


class TestEmptyGroup:
    def test_an_empty_group_selects_nothing(self) -> None:
        root = click.Group(name=None)
        root.add_command(click.Group(name="empty"))
        menu = Menu(root, "test")
        menu.enter()

        assert menu.items() == ()
        assert menu.selection() is None
        assert menu.enter() is None

    def test_moving_in_an_empty_group_is_harmless(self) -> None:
        root = click.Group(name=None)
        root.add_command(click.Group(name="empty"))
        menu = Menu(root, "test")
        menu.enter()

        menu.move(1)
        assert menu.cursor == 0


class TestForm:
    def test_reads_a_field_for_every_parameter_but_help(self) -> None:
        fields = read_fields(greet)

        assert [field.name for field in fields] == ["name", "shout"]

    def test_classifies_flags_choices_and_values(self) -> None:
        kinds = {field.name: field.kind for field in read_fields(choose)}

        assert kinds["mode"] is FieldKind.CHOICE
        assert kinds["needed"] is FieldKind.VALUE
        assert read_fields(greet)[1].kind is FieldKind.FLAG

    def test_carries_the_choices_so_they_can_be_offered(self) -> None:
        mode = next(field for field in read_fields(choose) if field.name == "mode")

        assert mode.choices == ("fast", "slow")

    def test_an_argument_has_no_option_string(self) -> None:
        upto = read_fields(count)[0]

        assert upto.is_argument() is True
        assert upto.required is True

    def test_a_required_field_left_empty_blocks_the_run(self) -> None:
        fields = read_fields(choose)
        values = {field.name: field.default for field in fields}

        assert missing_required(fields, values) == ("Needed",)

        values["needed"] = "yes"
        assert missing_required(fields, values) == ()

    def test_values_become_the_arguments_a_shell_would_take(self) -> None:
        fields = read_fields(greet)

        argv = to_argv(fields, {"name": "dexter", "shout": "true"})

        assert argv == ["--name", "dexter", "--shout"]

    def test_a_false_flag_contributes_nothing(self) -> None:
        fields = read_fields(greet)

        assert to_argv(fields, {"name": "", "shout": "false"}) == []

    def test_an_argument_is_passed_positionally(self) -> None:
        fields = read_fields(count)

        assert to_argv(fields, {"upto": "3"}) == ["3"]


class TestShellCommand:
    def test_renders_what_the_confirm_screen_shows(self) -> None:
        rendered = shell_command("./dx", ("numbers", "count"), ["3"])

        assert rendered == "./dx numbers count 3"

    def test_quotes_anything_a_shell_would_mangle(self) -> None:
        rendered = shell_command("./dx", ("greet",), ["--name", "two words"])

        assert rendered == "./dx greet --name 'two words'"
