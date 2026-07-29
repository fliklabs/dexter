"""Capturing a command's output, which is what lets the menu paint it into a pane."""

import pytest

from dexter.cli import Capture, CliConsole, CommandTree, Outcome, invoke
from dexter.dependency_injection import ContainerBuilder


class Recorder:
    """Collects every snapshot the runner reports."""

    def __init__(self) -> None:
        self.snapshots: list[str] = []

    def __call__(self, output: str) -> None:
        self.snapshots.append(output)


async def run_captured(
    builder: ContainerBuilder, argv: list[str]
) -> tuple[Outcome, Recorder]:
    """Invoke `argv` with its output captured, and return what happened."""
    container = builder.build()
    try:
        registry = await container.resolve(CommandTree)
        console = await container.resolve(CliConsole)
        recorder = Recorder()
        outcome = await invoke(
            registry.root,
            argv,
            container,
            prog_name="dx",
            capture=Capture(console, recorder),
        )
    finally:
        await container.aclose()
    return outcome, recorder


class TestCapture:
    async def test_captures_what_the_console_wrote(
        self, builder: ContainerBuilder
    ) -> None:
        outcome, _ = await run_captured(builder, ["speak"])

        assert "spoken" in outcome.output
        assert outcome.succeeded is True

    async def test_reports_output_as_it_is_produced(
        self, builder: ContainerBuilder
    ) -> None:
        """Snapshots are what the pane repaints from, so there must be at least one."""
        _, recorder = await run_captured(builder, ["speak"])

        assert recorder.snapshots
        assert "spoken" in recorder.snapshots[-1]

    async def test_strips_ansi_so_a_curses_pane_can_draw_it(
        self, builder: ContainerBuilder
    ) -> None:
        outcome, _ = await run_captured(builder, ["speak"])

        assert "\x1b[" not in outcome.output

    async def test_nothing_reaches_the_real_terminal(
        self, builder: ContainerBuilder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Anything escaping to stdout would corrupt the drawing."""
        await run_captured(builder, ["speak"])

        assert capsys.readouterr().out == ""

    async def test_the_console_is_restored_afterwards(
        self, builder: ContainerBuilder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        container = builder.build()
        try:
            registry = await container.resolve(CommandTree)
            console = await container.resolve(CliConsole)
            await invoke(
                registry.root,
                ["speak"],
                container,
                prog_name="dx",
                capture=Capture(console, Recorder()),
            )
            console.ok("after")
        finally:
            await container.aclose()

        assert "after" in capsys.readouterr().out


class TestFailures:
    async def test_a_raising_command_is_reported_not_propagated(
        self, builder: ContainerBuilder
    ) -> None:
        outcome, _ = await run_captured(builder, ["fail"])

        assert outcome.succeeded is False
        assert "RuntimeError" in outcome.output
        assert "meant to fail" in outcome.output

    async def test_system_exit_does_not_escape(self, builder: ContainerBuilder) -> None:
        """It would unwind past the menu and leave the terminal in raw mode."""
        outcome, _ = await run_captured(builder, ["depart"])

        assert outcome.exit_code == 4

    async def test_a_usage_error_is_captured_too(
        self, builder: ContainerBuilder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        outcome, _ = await run_captured(builder, ["greet", "--nope"])

        assert outcome.exit_code == 2
        assert "No such option" in outcome.output
        assert capsys.readouterr().err == ""


class TestWithoutCapture:
    async def test_output_goes_to_the_terminal(
        self, builder: ContainerBuilder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        container = builder.build()
        try:
            registry = await container.resolve(CommandTree)
            outcome = await invoke(registry.root, ["speak"], container, prog_name="dx")
        finally:
            await container.aclose()

        assert outcome.output == ""
        assert "spoken" in capsys.readouterr().out
