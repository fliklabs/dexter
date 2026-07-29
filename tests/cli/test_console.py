"""The shared console: the vocabulary commands write with, and redirecting all of it."""

import io

import pytest

from dexter.cli import CliConsole


class TestVocabulary:
    def test_ok_marks_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        CliConsole().ok("it worked")

        assert "✓" in capsys.readouterr().out

    def test_warn_marks_something_to_look_at(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        CliConsole().warn("look at this")

        printed = capsys.readouterr().out
        assert "⚠" in printed
        assert "look at this" in printed

    def test_error_marks_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        CliConsole().error("it broke")

        assert "✗" in capsys.readouterr().out

    def test_detail_is_supporting_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        CliConsole().detail("a note")

        assert "a note" in capsys.readouterr().out

    def test_heading_starts_a_section(self, capsys: pytest.CaptureFixture[str]) -> None:
        CliConsole().heading("results")

        assert "results" in capsys.readouterr().out

    def test_print_writes_markup(self, capsys: pytest.CaptureFixture[str]) -> None:
        CliConsole().print("[bold]loud[/]")

        printed = capsys.readouterr().out
        assert "loud" in printed
        assert "[bold]" not in printed, "markup was not interpreted"

    def test_a_table_renders_its_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        console = CliConsole()
        table = console.table("Module", "Covered")
        table.add_row("dexter.cli", "97%")
        console.print(table)

        printed = capsys.readouterr().out
        assert "Module" in printed
        assert "dexter.cli" in printed

    def test_the_underlying_console_is_reachable(self) -> None:
        """For anything the helpers do not cover."""
        assert CliConsole().console.width > 0


class TestCapture:
    def test_redirects_everything_into_the_target(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        console = CliConsole()
        buffer = io.StringIO()

        with console.capture(buffer):
            console.ok("captured")

        assert "captured" in buffer.getvalue()
        assert capsys.readouterr().out == ""

    def test_restores_the_previous_console_afterwards(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        console = CliConsole()

        with console.capture(io.StringIO()):
            console.ok("inside")
        console.ok("outside")

        assert "outside" in capsys.readouterr().out

    def test_restores_even_when_the_body_raises(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        console = CliConsole()

        with pytest.raises(RuntimeError), console.capture(io.StringIO()):
            raise RuntimeError("boom")
        console.ok("outside")

        assert "outside" in capsys.readouterr().out

    def test_writes_no_ansi_so_a_curses_pane_can_draw_it(self) -> None:
        console = CliConsole()
        buffer = io.StringIO()

        with console.capture(buffer):
            console.error("[bold]styled[/]")

        assert "\x1b[" not in buffer.getvalue()

    def test_the_width_is_fixed_so_tables_do_not_collapse(self) -> None:
        """Without a terminal rich falls back to 80 columns and truncates."""
        console = CliConsole()
        buffer = io.StringIO()

        with console.capture(buffer, width=200):
            assert console.console.width == 200
