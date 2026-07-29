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

from dexter.cli import CliConsole, CommandTree, inject, register_command, use_cli
from dexter.cli.interactive import navigator
from dexter.cli.interactive.navigator import navigate
from dexter.dependency_injection import Container, ContainerBuilder

from .conftest import Ledger
from .screen import FakeScreen, Key, fake_curs_set

DISMISS = ord("q")
"""Any key closes the output pane."""

ABORTED = 130
"""What a command that was stopped reports, the same as one that was aborted."""


@click.command("linger")
@inject
async def linger(scope: Container) -> None:
    """Run until stopped. Stands in for a server."""
    console = await scope.resolve(CliConsole)
    ledger = await scope.resolve(Ledger)
    console.ok("serving")
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
