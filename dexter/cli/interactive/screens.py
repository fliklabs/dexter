"""The screens: a list, a form, a confirmation, and a pager.

Each is a synchronous function that draws until the user decides something, then returns it.
Only the navigator is async, and only because running a command is.
"""

import curses
from typing import Any

from ..models import Field, FieldKind, missing_required
from .menu import Menu
from .rendering import (
    BACKSPACE,
    ENTER,
    ESC,
    REDRAW,
    body_height,
    footer,
    header,
    read_key,
    write,
)

_LABEL_WIDTH = 22
_SUBMENU = "  \u203a"
"""Marks a row that opens a submenu."""

_PRINTABLE = range(32, 127)
"""Keys an inline editor accepts. Anything else is a control or a multi-byte sequence."""


def list_screen(screen: Any, menu: Menu) -> bool:
    """Draw the open group until the user picks a row or goes back.

    Returns True when something was selected — the menu has already opened it if it was a
    group — and False to go back a level.
    """
    pending_escape = False

    while True:
        screen.erase()
        header(screen, menu.breadcrumb())
        rows = menu.rows()
        visible = body_height(screen)
        top = _scroll_top(menu.cursor, visible)

        for offset, (name, description, opens) in enumerate(rows[top : top + visible]):
            index = top + offset
            marker = _SUBMENU if opens else ""
            label = f"  {name}{marker}"
            attribute = curses.A_REVERSE if index == menu.cursor else curses.A_NORMAL
            write(
                screen,
                offset + 1,
                0,
                f"{label:<{_LABEL_WIDTH}}{description}",
                attribute,
            )

        if not rows:
            write(screen, 1, 2, "(nothing registered here)", curses.A_DIM)

        if pending_escape:
            footer(screen, "ESC again to leave")
        else:
            footer(
                screen,
                "↑↓ move   Enter select   ESC " + ("leave" if menu.at_root else "back"),
            )
        screen.refresh()

        key = read_key(screen)
        if key == REDRAW:
            continue

        if key == ESC:
            if not menu.at_root:
                menu.back()
                return False
            if pending_escape:
                return False
            # One ESC at the root is far more often a mistyped "back" than an intent to
            # leave, so it is confirmed rather than obeyed.
            pending_escape = True
            continue
        pending_escape = False

        if key in ENTER and rows:
            return True
        _move(menu, key, visible)


def _move(menu: Menu, key: int, page: int) -> None:
    """Apply a navigation key to the cursor."""
    steps = {
        curses.KEY_UP: -1,
        curses.KEY_DOWN: 1,
        curses.KEY_PPAGE: -page,
        curses.KEY_NPAGE: page,
    }
    if key in steps:
        menu.move(steps[key])


def params_screen(
    screen: Any, name: str, fields: tuple[Field, ...]
) -> dict[str, str] | None:
    """Fill in a command's parameters. Returns the values, or `None` if cancelled."""
    values = {field.name: field.default for field in fields}
    cursor = 0
    message = ""

    while True:
        screen.erase()
        header(screen, f"{name} — arguments")
        missing = missing_required(fields, values)
        write(screen, 1, 0, _run_row(name, missing), _attribute(cursor == 0))

        for index, field in enumerate(fields):
            write(
                screen,
                index + 2,
                0,
                _field_row(field, values[field.name]),
                _attribute(cursor == index + 1),
            )

        if message:
            write(screen, len(fields) + 3, 2, message, curses.A_BOLD)
        footer(screen, "↑↓ move   Enter edit or run   ESC cancel")
        screen.refresh()

        key = read_key(screen)
        if key == REDRAW:
            continue
        message = ""

        if key == ESC:
            return None
        if key == curses.KEY_UP:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_DOWN:
            cursor = min(len(fields), cursor + 1)
        elif key in ENTER:
            if cursor == 0:
                if missing:
                    message = f"Still needed: {', '.join(missing)}"
                    continue
                return values
            message = _edit(screen, fields[cursor - 1], values, cursor + 1)


def confirm_screen(screen: Any, shell: str) -> bool:
    """Show the equivalent shell command and wait for a decision.

    The command is shown rather than just run because it is the one moment where the menu can
    teach its own scriptable form — the reader sees exactly what to type next time.
    """
    while True:
        screen.erase()
        header(screen, "Confirm")
        write(screen, 2, 2, "This will run:", curses.A_DIM)
        write(screen, 3, 2, shell, curses.A_BOLD)
        footer(screen, "Enter run   ESC cancel")
        screen.refresh()

        key = read_key(screen)
        if key == REDRAW:
            continue
        if key in ENTER:
            return True
        if key == ESC:
            return False


def output_screen(screen: Any, title: str, text: str, *, live: bool = False) -> None:
    """Show a command's output, scrollable once it has finished.

    While `live`, the view is pinned to the end so output appears as it is produced.
    """
    lines = text.splitlines() or ["(no output)"]
    visible = body_height(screen)
    offset = max(0, len(lines) - visible) if live else 0

    while True:
        screen.erase()
        header(screen, title)
        for row, line in enumerate(lines[offset : offset + visible]):
            write(screen, row + 1, 0, line)
        if live:
            footer(screen, "running…")
            screen.refresh()
            return
        footer(screen, "↑↓ scroll   any other key returns")
        screen.refresh()

        key = read_key(screen)
        if key == REDRAW:
            continue
        if key == curses.KEY_UP:
            offset = max(0, offset - 1)
        elif key == curses.KEY_DOWN:
            offset = min(max(0, len(lines) - visible), offset + 1)
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - visible)
        elif key == curses.KEY_NPAGE:
            offset = min(max(0, len(lines) - visible), offset + visible)
        else:
            return


# ── internals ────────────────────────────────────────────────────────


def _scroll_top(cursor: int, visible: int) -> int:
    """The first row to draw so that `cursor` is on screen."""
    if cursor < visible:
        return 0
    return cursor - visible + 1


def _attribute(selected: bool) -> int:
    return curses.A_REVERSE if selected else curses.A_NORMAL


def _run_row(name: str, missing: tuple[str, ...]) -> str:
    if missing:
        return f"  ✗ Run {name}  (fill in {', '.join(missing)})"
    return f"  {_SUBMENU.strip()} Run {name}"


def _field_row(field: Field, value: str) -> str:
    marker = " *" if field.required else ""
    shown = value or "<empty>"
    hint = f"   {field.help}" if field.help else ""
    return f"  {field.label}{marker}: {shown}{hint}"


def _edit(screen: Any, field: Field, values: dict[str, str], row: int) -> str:
    """Edit one field in place. Returns a message to show, or an empty string."""
    if field.kind is FieldKind.FLAG:
        values[field.name] = "false" if values[field.name] == "true" else "true"
        return ""
    if field.kind is FieldKind.CHOICE:
        # Cycled rather than typed, so an invalid choice cannot be entered at all.
        choices = field.choices
        current = values[field.name]
        position = choices.index(current) if current in choices else -1
        values[field.name] = choices[(position + 1) % len(choices)]
        return ""

    typed = _inline_edit(screen, row, f"  {field.label}: ", values[field.name])
    if typed is not None:
        values[field.name] = typed
    return ""


def _inline_edit(screen: Any, row: int, prefix: str, initial: str) -> str | None:
    """A one-line editor drawn over the field's own row. Returns None if cancelled."""
    text = initial
    curses.curs_set(1)
    try:
        while True:
            _, width = screen.getmaxyx()
            write(screen, row, 0, " " * (width - 1))
            write(screen, row, 0, f"{prefix}{text}", curses.A_REVERSE)
            footer(screen, "Enter accept   ESC cancel")
            screen.refresh()

            key = screen.getch()
            if key in ENTER:
                return text
            if key == ESC:
                return None
            if key in BACKSPACE:
                text = text[:-1]
            elif key in _PRINTABLE:
                text += chr(key)
    finally:
        curses.curs_set(0)
