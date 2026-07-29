"""Getting text onto the clipboard, and being honest about it when that cannot be done."""

import subprocess
from typing import Any, Self

import pytest

from dexter.cli import clipboard


class Run:
    """Records what was handed to which command, standing in for `subprocess.run`."""

    def __init__(self, *, fails: bool = False) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.fails = fails

    def __call__(self, command: Any, **kwargs: Any) -> Any:
        if self.fails:
            raise subprocess.CalledProcessError(1, command)
        self.calls.append((tuple(command), kwargs["input"].decode()))
        return None


@pytest.fixture
def no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with none of the platform clipboard tools installed."""
    monkeypatch.setattr("shutil.which", lambda name: None)


def _installed(*names: str) -> Any:
    return lambda name: f"/usr/bin/{name}" if name in names else None


class TestTools:
    def test_it_uses_the_platform_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = Run()
        monkeypatch.setattr("shutil.which", _installed("pbcopy"))
        monkeypatch.setattr("subprocess.run", run)

        assert clipboard.copy("hello") is True
        assert run.calls == [(("pbcopy",), "hello")]

    def test_it_skips_the_ones_that_are_not_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = Run()
        monkeypatch.setattr("shutil.which", _installed("xclip"))
        monkeypatch.setattr("subprocess.run", run)

        clipboard.copy("hello")

        assert run.calls[0][0] == ("xclip", "-selection", "clipboard")

    def test_a_tool_that_fails_falls_through(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Installed is not the same as working: a broken `xclip` must not be the end of it."""
        monkeypatch.setattr("shutil.which", _installed("xclip"))
        monkeypatch.setattr("subprocess.run", Run(fails=True))
        monkeypatch.setattr("pathlib.Path.open", _unopenable)

        assert clipboard.copy("hello") is True
        assert "\033]52;c;" in capsys.readouterr().out


class TestEscapeSequence:
    def test_it_writes_osc_52_to_the_terminal(
        self, no_tools: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written: list[str] = []
        monkeypatch.setattr("pathlib.Path.open", _recording(written))

        assert clipboard.copy("hello") is True
        assert written == ["\033]52;c;aGVsbG8=\a"]

    def test_it_falls_back_to_stdout_without_a_terminal(
        self,
        no_tools: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Over SSH or under a test runner there may be no `/dev/tty` to open."""
        monkeypatch.setattr("pathlib.Path.open", _unopenable)

        assert clipboard.copy("hello") is True
        assert capsys.readouterr().out == "\033]52;c;aGVsbG8=\a"

    def test_it_reports_failure_when_nothing_can_be_written(
        self, no_tools: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pathlib.Path.open", _unopenable)
        monkeypatch.setattr("sys.stdout", _Closed())

        assert clipboard.copy("hello") is False


class TestNothingToCopy:
    def test_empty_text_goes_nowhere(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = Run()
        monkeypatch.setattr("shutil.which", _installed("pbcopy"))
        monkeypatch.setattr("subprocess.run", run)

        assert clipboard.copy("") is False
        assert run.calls == []


# ── stand-ins ────────────────────────────────────────────────────────


class _Terminal:
    """A writable stand-in for whatever `/dev/tty` opens to."""

    def __init__(self, written: list[str]) -> None:
        self.written = written

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Closed:
    """A stream that refuses to be written to."""

    def write(self, text: str) -> None:
        raise OSError("closed")

    def flush(self) -> None:
        return None


def _recording(written: list[str]) -> Any:
    return lambda self, *args, **kwargs: _Terminal(written)


def _unopenable(self: Any, *args: Any, **kwargs: Any) -> Any:
    raise OSError("no such device")
