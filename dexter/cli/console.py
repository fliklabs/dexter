"""The shared console, and the colour vocabulary every command uses.

One console rather than each command building its own, for two reasons. It keeps the colours
meaning the same thing everywhere — a reader learns them once — and it gives the interactive
layer a single place to redirect output into, which is what lets a command's output be
captured and painted into a curses pane without the command knowing anything about it.

Colour vocabulary:

| Colour | Means |
| --- | --- |
| green | succeeded, healthy |
| yellow | warning, pending, a dry run |
| red | failed |
| cyan | a name: a command, a file, an identifier |
| dim | detail that should not compete with the result |
"""

import contextlib
from collections.abc import Iterator
from typing import IO, Any

from rich.box import SIMPLE_HEAD
from rich.console import Console
from rich.table import Table

ACCENT = "cyan"
"""Names: commands, files, identifiers."""

OK = "green"
WARN = "yellow"
ERROR = "red"
DETAIL = "dim"


class CliConsole:
    """Writes a command's output.

    Registered as a singleton by `use_cli`, so every command shares one — and so the
    interactive layer can redirect all of them at once with `capture`.
    """

    __slots__ = ("_console",)

    def __init__(self) -> None:
        """Start writing to the real terminal."""
        self._console = Console(highlight=False)

    @property
    def console(self) -> Console:
        """The underlying rich console, for anything the helpers below do not cover."""
        return self._console

    def print(self, *renderables: Any) -> None:
        """Write a line, interpreting rich markup."""
        self._console.print(*renderables)

    def ok(self, message: str) -> None:
        """Report something that succeeded."""
        self._console.print(f"[{OK}]✓[/] {message}")

    def warn(self, message: str) -> None:
        """Report something that needs attention but is not a failure."""
        self._console.print(f"[{WARN}]⚠[/]  {message}")

    def error(self, message: str) -> None:
        """Report a failure."""
        self._console.print(f"[{ERROR}]✗[/] {message}")

    def detail(self, message: str) -> None:
        """Write supporting detail that should not compete with the result."""
        self._console.print(f"[{DETAIL}]{message}[/]")

    def heading(self, title: str) -> None:
        """Start a section."""
        self._console.print()
        self._console.print(f"[bold]{title}[/]")

    def table(self, *columns: str, title: str | None = None) -> Table:
        """Build a table styled like every other table in the CLI."""
        return Table(
            *columns,
            title=title,
            box=SIMPLE_HEAD,
            header_style=f"bold {ACCENT}",
            border_style=DETAIL,
            show_edge=False,
        )

    @contextlib.contextmanager
    def capture(self, target: IO[str], *, width: int = 120) -> Iterator[None]:
        """Redirect everything written here into `target` for the duration.

        The interactive layer uses this to paint a command's output into a curses pane.
        Colour and terminal detection are turned off — the pane cannot render ANSI — and the
        width is fixed so a table does not silently collapse to the 80-column fallback that
        rich uses when it cannot detect a terminal.
        """
        previous = self._console
        self._console = Console(
            file=target,
            highlight=False,
            no_color=True,
            force_terminal=False,
            width=width,
        )
        try:
            yield
        finally:
            self._console = previous
