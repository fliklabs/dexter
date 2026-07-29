"""What `run` does with no arguments, which depends entirely on whether there is a terminal."""

from typing import Any

import pytest

from dexter.cli import run
from dexter.dependency_injection import ContainerBuilder


class FakeStream:
    """Stands in for stdin or stdout, with a terminal or without one.

    `write` and `flush` are not decoration: a stand-in for `sys.stdout` that cannot be written
    to breaks everything downstream of it. Neither is `encoding` — without it `click.echo`
    decides this is a binary stream and writes bytes, which no real `sys.stdout` would do.
    """

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def pretend_terminal(monkeypatch: pytest.MonkeyPatch, *, tty: bool) -> None:
    """Make the process look interactive, or piped.

    Called from the test body rather than from a fixture, and that matters: pytest installs
    its own output capture at the start of the call phase, which is *after* fixtures have run
    — so a fixture patching `sys.stdout` is silently overwritten before the test starts, and
    the test then exercises the real environment while appearing to control it.
    """
    monkeypatch.setattr("sys.stdin", FakeStream(tty=tty))
    monkeypatch.setattr("sys.stdout", FakeStream(tty=tty))


def watch_menu(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    """Replace the menu with something that records that it was opened.

    The menu itself needs a real terminal, so only the hand-off is asserted here; the
    decisions it makes afterwards are covered by `test_menu.py`.
    """

    async def fake_navigate(
        root: Any, container: Any, *, prog_name: str, title: str
    ) -> int:
        opened.append(title)
        return 0

    monkeypatch.setattr("dexter.cli.interactive.navigate", fake_navigate)


class TestWithoutATerminal:
    async def test_prints_help_rather_than_opening_a_menu(
        self,
        builder: ContainerBuilder,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Piped or under CI, a curses menu cannot work and must not be attempted.

        Nothing is faked here. A test run is already the case being tested — pytest's own
        capture means there is no terminal — so this asserts against the real code path.
        """

        async def explode(*args: Any, **kwargs: Any) -> int:
            raise AssertionError("the menu was opened without a terminal")

        monkeypatch.setattr("dexter.cli.interactive.navigate", explode)

        container = builder.build()
        try:
            assert await run(container, [], prog_name="dx") == 0
        finally:
            await container.aclose()

        printed = capsys.readouterr().out
        assert "Usage: dx" in printed
        assert "greet" in printed

    async def test_arguments_still_work_without_a_terminal(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pretend_terminal(monkeypatch, tty=False)

        container = builder.build()
        try:
            assert await run(container, ["greet"]) == 0
        finally:
            await container.aclose()


class TestWithATerminal:
    async def test_no_arguments_opens_the_menu(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []
        pretend_terminal(monkeypatch, tty=True)
        watch_menu(monkeypatch, opened)

        container = builder.build()
        try:
            assert await run(container, [], prog_name="dx", title="tests") == 0
        finally:
            await container.aclose()

        assert opened == ["tests"]

    async def test_the_title_defaults_to_the_program_name(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []
        pretend_terminal(monkeypatch, tty=True)
        watch_menu(monkeypatch, opened)

        container = builder.build()
        try:
            await run(container, [], prog_name="dx")
        finally:
            await container.aclose()

        assert opened == ["dx"]

    async def test_arguments_never_reach_the_menu(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scripted invocation must not open a menu even at an interactive terminal."""
        pretend_terminal(monkeypatch, tty=True)

        async def explode(*args: Any, **kwargs: Any) -> int:
            raise AssertionError("the menu was opened for a scripted invocation")

        monkeypatch.setattr("dexter.cli.interactive.navigate", explode)

        container = builder.build()
        try:
            assert await run(container, ["greet"]) == 0
        finally:
            await container.aclose()
