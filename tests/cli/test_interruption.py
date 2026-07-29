"""Stopping a command that is still running.

The property under test is one the menu did not have: while a command runs, the screen is
still being watched. Without it nothing that fails to finish on its own — a server, a watch
loop, a wedged subprocess — could ever be stopped without killing the whole terminal.

A dedicated command tree is used rather than the shared one, because the root rows are
alphabetical and every script in `test_navigator.py` counts keystrokes by position. Adding a
row there would silently shift them all.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any

import click
import pytest

from dexter.cli import (
    CliConsole,
    CommandTree,
    clipboard,
    inject,
    register_command,
    use_cli,
)
from dexter.cli.interactive import navigator
from dexter.cli.interactive.navigator import navigate
from dexter.dependency_injection import Container, ContainerBuilder

from .conftest import Ledger
from .screen import FakeScreen, Key, drag, fake_curs_set, press, release

DISMISS = ord("q")
"""Any key closes the output pane."""

ABORTED = 130
"""What a command that was stopped reports, the same as one that was aborted."""


CHATTER = 40
"""Lines `linger` prints before it blocks.

More than fits on a screen, on purpose: a view can only be scrolled when there is something
above it, so a command producing two lines could not exercise the pane at all.
"""


@click.command("linger")
@inject
async def linger(scope: Container) -> None:
    """Run until stopped. Stands in for a server."""
    console = await scope.resolve(CliConsole)
    ledger = await scope.resolve(Ledger)
    console.ok("serving")
    for tick in range(CHATTER):
        console.detail(f"tick {tick:02d}")
    try:
        await asyncio.Event().wait()  # never set: only cancellation ends this
    finally:
        ledger.entries.append("stopped")


@click.command("brief")
@inject
async def brief(scope: Container) -> None:
    """Finish immediately."""
    console = await scope.resolve(CliConsole)
    console.ok("done")


@pytest.fixture
def copied(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record what reached the clipboard rather than putting it on the real one."""
    taken: list[str] = []

    def fake_copy(text: str) -> bool:
        taken.append(text)
        return True

    monkeypatch.setattr(clipboard, "copy", fake_copy)
    return taken


@pytest.fixture
def builder(ledger: Ledger) -> ContainerBuilder:
    """A tree of two rows: `brief` first, then `linger`."""
    container_builder = ContainerBuilder()
    container_builder.register(Ledger).to_instance(ledger)
    use_cli(container_builder)
    register_command(container_builder, brief)
    register_command(container_builder, linger)
    return container_builder


async def drive(
    builder: ContainerBuilder,
    monkeypatch: pytest.MonkeyPatch,
    *keys: Key,
) -> tuple[int, FakeScreen]:
    """Run the menu against a scripted keyboard, with the poll interval removed."""
    fake_curs_set(monkeypatch)
    # Zero rather than 30ms: the loop still yields the event loop every turn, so the command
    # runs exactly as it would, and the suite does not spend real time waiting.
    monkeypatch.setattr(navigator, "_TICK", 0)
    screen = FakeScreen(keys)

    @contextlib.contextmanager
    def fake_terminal() -> Iterator[Any]:
        yield screen

    monkeypatch.setattr(navigator, "terminal", fake_terminal)

    container = builder.build()
    try:
        tree = await container.resolve(CommandTree)
        exit_code = await navigate(
            tree.root, container, prog_name="./dx", title="tests"
        )
    finally:
        await container.aclose()
    return exit_code, screen


class TestStoppingARunningCommand:
    async def test_confirming_stops_it_and_returns_to_the_menu(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        exit_code, screen = await drive(
            builder,
            monkeypatch,
            "down",  # to `linger`
            "enter",  # select
            "enter",  # confirm the shell command
            "ctrl-c",  # raise the modal over the running command
            "right",  # move to Stop
            "enter",  # confirm
            DISMISS,  # close the output pane
            "esc",
            "esc",  # leave the menu
        )

        assert exit_code == ABORTED
        assert "stopped" in ledger.entries
        assert "tests" in screen.text()

    async def test_the_modal_names_the_command_it_would_stop(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert "Stop linger?" in screen.text()
        assert "Keep running" in screen.text()

    async def test_a_second_interrupt_stops_it_without_choosing(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        """Reaching for Ctrl+C twice is not an accident."""
        exit_code, _ = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "ctrl-c",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == ABORTED
        assert "stopped" in ledger.entries

    async def test_escaping_the_modal_leaves_it_running(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        """Declining must not stop it — the command has to still be there afterwards."""
        exit_code, _ = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "esc",  # decline; the command keeps running
            "ctrl-c",  # ask again
            "right",
            "enter",  # and mean it this time
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == ABORTED
        assert ledger.entries.count("stopped") == 1

    async def test_the_output_it_produced_before_stopping_survives(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert "serving" in screen.text()


class TestTheModal:
    async def test_is_drawn_as_a_bordered_box(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It interrupts output that is still arriving, so it has to look like it does."""
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        drawn = screen.text()
        assert "┌" in drawn
        assert "┐" in drawn
        assert "└" in drawn
        assert "┘" in drawn
        assert "│" in drawn

    async def test_floats_over_the_output_rather_than_replacing_it(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pane follows the end, so its last line must survive under the box."""
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        boxed = next(
            frame for frame in screen.frames if any("┌" in row for row in frame)
        )
        assert any(f"tick {CHATTER - 1:02d}" in row for row in boxed)


class TestScrollingWhileItRuns:
    async def test_a_scroll_key_pauses_the_view(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pane that always jumped to the end could not be read while output arrived."""
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "pgup",  # look back
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert "paused" in screen.text()

    async def test_end_resumes_following(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "pgup",
            "end",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        # The last thing drawn before the modal went up is following again.
        following = [
            frame for frame in screen.frames if any("running…" in r for r in frame)
        ]
        assert following, "never returned to following the end"

    async def test_scrolling_does_not_stop_the_command(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch, ledger: Ledger
    ) -> None:
        exit_code, _ = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "pgup",
            "pgdn",
            "up",
            "down",
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == ABORTED
        assert ledger.entries.count("stopped") == 1


class TestSelectingWithTheMouse:
    """Dragging across a running command's output and copying what was dragged.

    The unit tests in `test_pane.py` cover what a drag means; these cover that a mouse report
    reaches the pane at all — that the reports are fetched in the right order, that the
    highlight is painted, and that the toast is drawn over output that is still arriving.
    """

    async def test_a_drag_copies_what_it_crossed(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        copied: list[str],
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            press(1, 0),
            drag(2, 7),
            release(2, 7),
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert len(copied) == 1
        assert "Copied to clipboard" in screen.text()

    async def test_the_selection_is_highlighted_while_it_is_dragged(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        copied: list[str],
    ) -> None:
        """Reverse video does not survive a `FakeScreen`, so the paint order is what is checked."""
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            press(1, 0),
            drag(1, 4),
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert "paused" in screen.text()

    async def test_a_press_and_a_release_copy_without_any_motion(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        copied: list[str],
    ) -> None:
        """What a terminal reporting only presses and releases sends — most of them."""
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            press(1, 0),
            release(3, 5),
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert len(copied) == 1
        assert "Copied to clipboard" in screen.text()

    async def test_a_click_alone_copies_nothing(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        copied: list[str],
    ) -> None:
        _, screen = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            press(2, 3),
            release(2, 3),
            "ctrl-c",
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert copied == []
        assert "Copied to clipboard" not in screen.text()

    async def test_the_mouse_does_not_answer_the_modal(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        copied: list[str],
        ledger: Ledger,
    ) -> None:
        """A drag begun behind the box would be one nobody could see."""
        exit_code, _ = await drive(
            builder,
            monkeypatch,
            "down",
            "enter",
            "enter",
            "ctrl-c",
            press(1, 0),
            drag(2, 4),
            release(2, 4),
            "right",
            "enter",
            DISMISS,
            "esc",
            "esc",
        )

        assert exit_code == ABORTED
        assert copied == []


class TestCommandsThatFinishOnTheirOwn:
    async def test_a_quick_command_is_unaffected(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The watcher must not change what an ordinary command does."""
        exit_code, screen = await drive(
            builder, monkeypatch, "enter", "enter", DISMISS, "esc", "esc"
        )

        assert exit_code == 0
        assert "done" in screen.text()

    async def test_a_quick_command_does_not_swallow_the_next_keystroke(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key read while watching is a key gone.

        A command that finishes immediately must never reach the poll, or the keystroke meant
        for the screen after it disappears — which is exactly what the whole `test_navigator`
        suite would notice first.
        """
        exit_code, _ = await drive(
            builder, monkeypatch, "enter", "enter", DISMISS, "esc", "esc"
        )

        assert exit_code == 0
