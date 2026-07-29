"""The navigator loop: menu, form, confirmation, run, repeat.

The terminal is replaced with a fake window, so the whole loop runs here — including actually
invoking a command and painting its output.

The sample tree's root rows are alphabetical: `depart`, `exits`, `fail`, `greet`, `numbers`,
`speak`. Scripts below spell out the keystrokes rather than computing them, so a reader can
follow what a user would actually press.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from dexter.cli import CommandTree
from dexter.cli.interactive import navigator
from dexter.cli.interactive.navigator import navigate
from dexter.dependency_injection import ContainerBuilder

from .conftest import Ledger
from .screen import FakeScreen, Key, fake_curs_set

DISMISS = ord("q")
"""Any key closes the output pane."""


def use_screen(monkeypatch: pytest.MonkeyPatch, screen: FakeScreen) -> None:
    """Make `terminal()` hand out `screen` instead of taking over the real one."""

    @contextlib.contextmanager
    def fake_terminal() -> Iterator[Any]:
        yield screen

    monkeypatch.setattr(navigator, "terminal", fake_terminal)


async def drive(
    builder: ContainerBuilder,
    monkeypatch: pytest.MonkeyPatch,
    *keys: Key,
) -> tuple[int, FakeScreen]:
    """Run the menu against a scripted keyboard, returning the exit code and what was drawn."""
    fake_curs_set(monkeypatch)
    screen = FakeScreen(keys)
    use_screen(monkeypatch, screen)

    container = builder.build()
    try:
        tree = await container.resolve(CommandTree)
        exit_code = await navigate(
            tree.root, container, prog_name="./dx", title="tests"
        )
    finally:
        await container.aclose()
    return exit_code, screen


class TestLeaving:
    async def test_two_escapes_at_the_root_leave(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, screen = await drive(builder, monkeypatch, "esc", "esc")

        assert exit_code == 0
        assert "tests" in screen.text()

    async def test_confirming_an_interrupt_leaves(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, _ = await drive(builder, monkeypatch, "ctrl-c", "right", "enter")

        assert exit_code == 0


class TestRunning:
    async def test_runs_a_command_with_no_arguments(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, screen = await drive(
            builder,
            monkeypatch,
            # down to `speak`, select, confirm, dismiss, leave
            *("down", "down", "down", "down", "down"),
            "enter",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == 0
        assert "spoken" in screen.text()

    async def test_shows_the_shell_command_before_running(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is how the menu teaches its own scriptable form."""
        _, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down", "down", "down", "down"),
            "enter",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert "./dx speak" in screen.text()

    async def test_cancelling_at_the_confirmation_runs_nothing(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down", "down", "down", "down"),
            "enter",
            "esc",  # decline at the confirmation
            "esc",
            "esc",
        )

        assert "spoken" not in screen.text()

    async def test_a_failing_command_is_reported_not_raised(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down"),  # `fail`
            "enter",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == 1
        assert "RuntimeError" in screen.text()
        assert "exit 1" in screen.text()

    async def test_the_exit_code_of_the_last_command_is_returned(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, _ = await drive(
            builder,
            monkeypatch,
            "down",  # `exits`, which returns 3
            "enter",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == 3


class TestArguments:
    async def test_fills_in_a_form_before_running(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        await drive(
            builder,
            monkeypatch,
            *("down", "down", "down"),  # `greet`
            "enter",
            *("down", "down", "enter"),  # toggle `shout`
            *("up", "up", "enter"),  # back to the run row, submit
            "enter",  # confirm
            DISMISS,
            "esc",
            "esc",
        )

        assert ledger.entries == ["WORLD"]

    async def test_the_shell_command_carries_the_arguments(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down", "down"),
            "enter",
            *("down", "down", "enter"),
            *("up", "up", "enter"),
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        # Defaults are carried explicitly, so what is shown is exactly what will run rather
        # than a shorter command that merely happens to behave the same today.
        assert "./dx greet --name world --shout" in screen.text()

    async def test_cancelling_the_form_runs_nothing(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        await drive(
            builder,
            monkeypatch,
            *("down", "down", "down"),
            "enter",
            "esc",  # abandon the form
            "esc",
            "esc",
        )

        assert ledger.entries == []


class TestNesting:
    async def test_opens_a_group_and_runs_from_inside_it(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        await drive(
            builder,
            monkeypatch,
            *("down", "down", "down", "down"),  # `numbers`
            "enter",
            "down",  # `count`, which takes one positional argument
            "enter",
            *("down", "enter", ord("2"), "enter"),  # fill the argument in
            *("up", "enter"),  # back to the run row, submit
            "enter",  # confirm
            DISMISS,
            "esc",  # back to the root
            "esc",
            "esc",
        )

        assert ledger.entries == ["0", "1"]

    async def test_the_shell_command_carries_the_whole_path(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down", "down", "down"),
            "enter",
            "down",
            "enter",
            *("down", "enter", ord("2"), "enter"),
            *("up", "enter"),
            "enter",
            DISMISS,
            "esc",
            "esc",
            "esc",
        )

        assert "./dx numbers count 2" in screen.text()

    async def test_escape_in_a_submenu_returns_to_the_parent(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not out of the menu entirely — one ESC is one level, wherever you are."""
        exit_code, screen = await drive(
            builder,
            monkeypatch,
            *("down", "down", "down", "down"),  # `numbers`
            "enter",
            "esc",  # back to the root, which must still be showing
            # The root remembers its cursor, so climb back to the first row.
            *("up", "up", "up", "up"),
            "enter",  # and it is still usable: select `depart`
            "enter",  # confirm
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == 4, "the root menu was not usable after coming back to it"
        assert "numbers" in screen.text()
