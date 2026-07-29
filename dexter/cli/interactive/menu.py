"""The menu's state, with no terminal anywhere near it.

Everything about *where you are* lives here: which group is open, where the cursor sits, how
you got there, and what going back means. None of it touches curses, so all of it is testable
against a plain click tree — which matters, because this is the part with the behaviour worth
getting right, and the drawing is just drawing.

The stack is what makes nesting free. Entering a group pushes; going back pops. Each level
remembers its own cursor, so backing out of a submenu returns you to the row you left from
rather than to the top.
"""

import click

from ..models import children, describe_command, is_group

_SEPARATOR = "  \u203a  "
"""Between breadcrumb segments. The glyph, not a typo for ">"."""


class Level:
    """One open group, and where the cursor was left in it."""

    __slots__ = ("cursor", "group", "name")

    def __init__(self, group: click.Group, name: str) -> None:
        """Record the group, the path segment that reached it, and a cursor at the top."""
        self.group = group
        self.name = name
        self.cursor = 0


class Menu:
    """Where the user is in the command tree."""

    __slots__ = ("_stack", "title")

    def __init__(self, root: click.Group, title: str) -> None:
        """Open the root group."""
        self._stack = [Level(root, "")]
        self.title = title

    @property
    def depth(self) -> int:
        """How many groups deep, counting the root as one."""
        return len(self._stack)

    @property
    def at_root(self) -> bool:
        """Whether going back would leave the menu entirely."""
        return len(self._stack) == 1

    @property
    def cursor(self) -> int:
        """The selected row in the open group."""
        return self._stack[-1].cursor

    def items(self) -> tuple[tuple[str, click.Command], ...]:
        """The rows of the open group, in the order they are drawn."""
        return children(self._stack[-1].group)

    def rows(self) -> tuple[tuple[str, str, bool], ...]:
        """Each row as (name, description, opens_a_submenu), ready to draw."""
        return tuple(
            (name, describe_command(command), is_group(command))
            for name, command in self.items()
        )

    def path(self) -> tuple[str, ...]:
        """The command path to the open group, for building the shell command."""
        return tuple(level.name for level in self._stack if level.name)

    def breadcrumb(self) -> str:
        """The header line: the title, then each group entered since."""
        return _SEPARATOR.join([self.title, *self.path()])

    def move(self, delta: int) -> None:
        """Move the cursor, stopping at either end rather than wrapping.

        Not wrapping is deliberate: on a long list, wrapping from the top to the bottom looks
        like the screen jumped somewhere else entirely.
        """
        count = len(self.items())
        if count == 0:
            return
        level = self._stack[-1]
        level.cursor = max(0, min(count - 1, level.cursor + delta))

    def selection(self) -> click.Command | None:
        """The command under the cursor, or `None` if the group is empty."""
        items = self.items()
        if not items:
            return None
        return items[self._stack[-1].cursor][1]

    def enter(self) -> click.Command | None:
        """Act on the selected row.

        Returns the command to run, or `None` when the selection was a group — in which case
        it has been opened and the caller should redraw.
        """
        selected = self.selection()
        if selected is None:
            return None
        if isinstance(selected, click.Group):
            self._stack.append(Level(selected, selected.name or ""))
            return None
        return selected

    def back(self) -> bool:
        """Close the open group. Returns False at the root, where there is nothing to close."""
        if self.at_root:
            return False
        self._stack.pop()
        return True

    def command_path(self, command: click.Command) -> tuple[str, ...]:
        """The full path a user would type to reach `command` from where they are."""
        return (*self.path(), command.name or "")
