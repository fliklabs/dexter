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
    WHEEL_LINES,
    body_height,
    footer,
    header,
    mouse_report,
    read_key,
    show_cursor,
    write,
)
from .selection import Selection

_LABEL_WIDTH = 22
_SUBMENU = "  \u203a"
"""Marks a row that opens a submenu."""

_PRINTABLE = range(32, 127)
"""Keys an inline editor accepts. Anything else is a control or a multi-byte sequence."""


def list_screen(screen: Any, menu: Menu) -> bool:
    """Draw the open group until the user picks a row or leaves the menu entirely.

    Returns True when something was selected, and False only when the user wants to leave.

    Going back is handled here rather than reported upwards, and that is load-bearing: the
    caller cannot tell "went back to the root" from "left from the root" once the level has
    been popped, so returning for both closed the menu when the user meant to go up one.
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
                continue
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


def output_screen(screen: Any, title: str, text: str) -> None:
    """Show a finished command's output, scrollable, until a key dismisses it.

    **A mouse report never dismisses this**, and that is the whole reason it is handled here at
    all. `getch` reports a wheel tick as `KEY_MOUSE`, one more key code that is not an arrow — so
    the obvious "any other key returns" sent a reader who reached for the wheel straight back to
    the menu, taking the output they were trying to read with it. Scrolling is the one gesture
    guaranteed not to mean "I am finished".
    """
    lines = _lines(text)
    visible = body_height(screen)
    offset = 0

    while True:
        _paint(screen, title, lines, offset, visible)
        footer(screen, "↑↓ or wheel scroll   any other key returns")
        screen.refresh()

        key = read_key(screen)
        if key == REDRAW:
            continue

        if key == curses.KEY_MOUSE:
            # Fetched because the direction is in the report and nowhere else — and fetched
            # *now*, because a report not taken before the next `getch` is gone. Nothing is
            # dragged here, so `dragging` is settled: this screen has no selection.
            report = mouse_report(screen)
            turn = 0 if report is None else report.wheel(dragging=False)
            offset = clamp(offset + turn * WHEEL_LINES, len(lines), visible)
            continue

        if key not in SCROLL_KEYS:
            return
        # `None` means "the end" — resolved to a line here rather than left as a sentinel,
        # because a finished command produces no more output and so has nothing to follow.
        # Treating it as zero would send a reader who scrolled to the bottom back to the top.
        offset = clamp(scrolled(offset, key, len(lines), visible), len(lines), visible)


def live_screen(
    screen: Any,
    title: str,
    text: str,
    offset: int | None = None,
    selection: Selection | None = None,
) -> None:
    """Show a running command's output.

    `offset` of `None` follows the end, so new output appears as it is produced. A number
    pins the view there instead — which is the whole reason this takes one. A pane that
    always jumped to the bottom could not be read while anything was still writing to it,
    and a long-running command is exactly the case where you want to look back.

    `selection`, when there is one, is painted over the text in reverse video.
    """
    lines = _lines(text)
    visible = body_height(screen)
    position = clamp(offset, len(lines), visible)

    _paint(screen, title, lines, position, visible)
    if selection is not None and not selection.empty:
        _highlight(screen, lines, position, visible, selection)
    if offset is None:
        footer(screen, "running…   ↑↓ scroll   drag to copy   Ctrl+C stop")
    else:
        showing = min(position + visible, len(lines))
        footer(
            screen,
            f"paused — showing {position + 1}-{showing} of {len(lines)}   "
            f"End follows   Ctrl+C stop",
        )
    screen.refresh()


def scrolled(offset: int | None, key: int, total: int, visible: int) -> int | None:
    """Apply one scroll key. `None` means following the end rather than pinned to a line.

    Scrolling back to the bottom returns `None`, so reaching the end resumes following
    instead of freezing one line short of it — which would look like the output had stopped.
    """
    if key == curses.KEY_END:
        return None
    if key == curses.KEY_HOME:
        return 0

    if key in _BY_LINE:
        delta = _BY_LINE[key]
    elif key in _BY_PAGE:
        delta = _BY_PAGE[key] * visible
    else:
        return offset

    bottom = max(0, total - visible)
    settled = max(0, min(clamp(offset, total, visible) + delta, bottom))
    return None if settled >= bottom else settled


def clamp(offset: int | None, total: int, visible: int) -> int:
    """Where to actually start drawing: the end when following, else a valid line.

    Shared rather than private because anything that maps a screen row back to a line of text
    has to agree with the paint about which line is on top, and two copies of this arithmetic
    would disagree the moment one of them was fixed.
    """
    bottom = max(0, total - visible)
    return bottom if offset is None else max(0, min(offset, bottom))


# ── internals ────────────────────────────────────────────────────────


_BY_LINE = {curses.KEY_UP: -1, curses.KEY_DOWN: 1}
_BY_PAGE = {curses.KEY_PPAGE: -1, curses.KEY_NPAGE: 1}
"""Kept apart rather than encoded as one number.

Folding them into a single table means a magnitude has to mean "and this one is a page", which
reads as a distance and multiplies as a count — a page key then moves two pages, quietly.
"""

SCROLL_KEYS = (*_BY_LINE, *_BY_PAGE, curses.KEY_HOME, curses.KEY_END)
"""Every key that scrolls rather than meaning something else."""


def _lines(text: str) -> list[str]:
    """The rows to draw, never empty."""
    return text.splitlines() or ["(no output)"]


def _paint(
    screen: Any, title: str, lines: list[str], offset: int, visible: int
) -> None:
    """Draw the header and one window's worth of output. The caller adds the footer."""
    screen.erase()
    header(screen, title)
    for row, line in enumerate(lines[offset : offset + visible]):
        write(screen, row + 1, 0, line)


def _highlight(
    screen: Any, lines: list[str], offset: int, visible: int, selection: Selection
) -> None:
    """Redraw the selected parts of the visible lines in reverse video.

    A second pass over the window rather than a branch inside `_paint`: the highlight is drawn
    *over* text that is already there, so the ordinary path stays one unconditional write per
    line and nothing about painting output changes because a mouse exists.
    """
    for row, line in enumerate(lines[offset : offset + visible]):
        span = selection.span(offset + row, len(line))
        if span is None:
            continue
        begins, ends = span
        if ends > begins:
            write(screen, row + 1, begins, line[begins:ends], curses.A_REVERSE)


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
    show_cursor(visible=True)
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
        show_cursor(visible=False)
