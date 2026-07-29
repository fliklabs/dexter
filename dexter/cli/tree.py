"""The tree of registered commands.

A `CommandTree` holds one root `click.Group` and grows it as commands are registered. It is
the *only* description of the CLI: the menu walks it, the non-interactive path parses against
it, and `--help` is generated from it. There is no second list to keep in step.

A *tree* rather than a registry, and named so deliberately: `dexter.cqrs` has a
`CommandRegistry` of its own for an entirely different kind of command, and two identically
named types in one dependency graph is a trap for whoever ends up importing both.

Groups are created on demand by name, so a consumer registering into `"example"` twice gets one
submenu with two commands rather than two submenus.
"""

import click

from .errors import DuplicateCommandError, InvalidCommandError


class CommandTree:
    """The commands a CLI offers, as nested click groups.

    Registered as an instance by `use_cli`, then populated by `register_command` while wiring —
    the same registry-mutation pattern the other dexter modules use.
    """

    __slots__ = ("_group_help", "_root")

    def __init__(self) -> None:
        """Start with an empty root group."""
        self._root = click.Group(name=None)
        self._group_help: dict[str, str] = {}

    @property
    def root(self) -> click.Group:
        """The root group, which is what everything else walks or parses against."""
        return self._root

    def add(
        self,
        command: click.Command,
        /,
        *,
        group: str | None = None,
        help: str = "",  # noqa: A002 - mirrors click's own keyword
    ) -> None:
        """Register `command`, optionally under a named group.

        Args:
            command: The command to register.
            group: Name of the submenu to place it under. Created on first use.
            help: One-line description for the group, used the first time it is created.
        """
        if not isinstance(command, click.Command):
            raise InvalidCommandError(command, "a command must be a `click.Command`.")
        if not command.name:
            raise InvalidCommandError(
                command, "a command must have a name; it is what a user types."
            )

        parent = self._root if group is None else self._group(group, help)
        if command.name in parent.commands:
            location = "the root" if group is None else f"`{group}`"
            raise DuplicateCommandError(
                f"a command named `{command.name}` is already registered under {location}."
            )
        parent.add_command(command)

    def group_names(self) -> tuple[str, ...]:
        """Every group name created so far, in creation order."""
        return tuple(self._group_help)

    def command_names(self, group: str | None = None) -> tuple[str, ...]:
        """The names registered directly under `group`, or under the root."""
        parent = self._root if group is None else self._group(group, "")
        return tuple(sorted(parent.commands))

    def _group(self, name: str, help: str) -> click.Group:  # noqa: A002 - as above
        existing = self._root.commands.get(name)
        if isinstance(existing, click.Group):
            return existing
        if existing is not None:
            raise DuplicateCommandError(
                f"`{name}` is already registered as a command, so it cannot also be a group."
            )
        created = click.Group(name=name, help=help or f"{name.capitalize()} commands.")
        self._root.add_command(created)
        self._group_help[name] = help
        return created
