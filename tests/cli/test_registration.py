"""Registering commands: what the tree ends up looking like, and what is rejected."""

from typing import Any

import click
import pytest

from dexter.cli import (
    CliConsole,
    CliNotWiredError,
    CommandTree,
    DuplicateCommandError,
    InvalidCommandError,
    register_command,
    use_cli,
)
from dexter.dependency_injection import ContainerBuilder

from .conftest import count, greet


class TestWhatIsRegistered:
    def test_use_cli_registers_the_registry_and_the_console(
        self, bare_builder: ContainerBuilder
    ) -> None:
        use_cli(bare_builder)

        assert bare_builder.is_registered(CommandTree)
        assert bare_builder.is_registered(CliConsole)

    def test_a_command_appears_at_the_root(self, builder: ContainerBuilder) -> None:
        registry = builder.resolve_instance(CommandTree)

        assert "greet" in registry.command_names()

    def test_a_grouped_command_appears_under_its_group(
        self, builder: ContainerBuilder
    ) -> None:
        registry = builder.resolve_instance(CommandTree)

        assert "numbers" in registry.command_names()
        assert registry.command_names("numbers") == ("choose", "count")

    def test_registering_into_one_group_twice_makes_one_submenu(
        self, builder: ContainerBuilder
    ) -> None:
        registry = builder.resolve_instance(CommandTree)

        assert registry.group_names() == ("numbers",)

    def test_the_group_keeps_the_help_it_was_created_with(
        self, builder: ContainerBuilder
    ) -> None:
        registry = builder.resolve_instance(CommandTree)
        group = registry.root.commands["numbers"]

        assert group.help == "Number things."


class TestGuards:
    def test_raises_when_use_cli_was_never_called(
        self, bare_builder: ContainerBuilder
    ) -> None:
        with pytest.raises(CliNotWiredError, match="use_cli"):
            register_command(bare_builder, greet)

    def test_raises_when_a_name_is_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(DuplicateCommandError, match="already registered"):
            register_command(builder, greet)

    def test_the_same_name_in_a_different_group_is_fine(
        self, builder: ContainerBuilder
    ) -> None:
        register_command(builder, greet, group="numbers")
        registry = builder.resolve_instance(CommandTree)

        assert "greet" in registry.command_names()
        assert "greet" in registry.command_names("numbers")

    def test_raises_when_a_group_name_is_already_a_command(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(DuplicateCommandError, match="cannot also be a group"):
            register_command(builder, count, group="greet")

    def test_rejects_something_that_is_not_a_command(
        self, builder: ContainerBuilder
    ) -> None:
        not_a_command: Any = "greet"
        with pytest.raises(InvalidCommandError, match=r"must be a `click\.Command`"):
            register_command(builder, not_a_command)

    def test_rejects_a_command_with_no_name(self, builder: ContainerBuilder) -> None:
        nameless = click.Command(name=None)

        with pytest.raises(InvalidCommandError, match="must have a name"):
            register_command(builder, nameless)
