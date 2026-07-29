"""What the interactive layer needs to know about a command, without knowing about curses.

click already describes every command and parameter; these types are that description reduced
to the few facts a menu and a form actually render. Keeping the reduction here rather than
inside the drawing code is what makes the interactive layer testable: the state machine works
on these, and only the painting needs a terminal.

Slotted rather than pydantic — dexter builds these for itself, one per parameter per render,
and there is no untrusted input to validate.
"""

import shlex
from enum import StrEnum
from typing import Any

import click


class FieldKind(StrEnum):
    """How a parameter is edited."""

    FLAG = "FLAG"
    """A boolean. Enter toggles it; there is nothing to type."""

    CHOICE = "CHOICE"
    """One of a fixed set. Enter cycles, so an invalid value cannot be entered at all."""

    VALUE = "VALUE"
    """Anything else. Enter opens an inline editor."""


def describe_kind(kind: FieldKind) -> str:
    """Render a kind as the symbol a caller would type, such as `FieldKind.FLAG`.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence.
    """
    return f"FieldKind.{kind.name}"


class Field:
    """One parameter of one command, as the form renders it."""

    __slots__ = (
        "choices",
        "default",
        "help",
        "kind",
        "label",
        "name",
        "option",
        "required",
    )

    def __init__(  # noqa: PLR0913 - a record; every one of these is a distinct fact
        self,
        name: str,
        label: str,
        kind: FieldKind,
        *,
        option: str | None,
        default: str,
        choices: tuple[str, ...],
        help: str,  # noqa: A002 - matches click's own attribute name
        required: bool,
    ) -> None:
        """Record everything the form needs to draw and edit this parameter."""
        self.name = name
        self.label = label
        self.kind = kind
        self.option = option
        self.default = default
        self.choices = choices
        self.help = help
        self.required = required

    def is_argument(self) -> bool:
        """Whether this is a positional argument rather than a named option."""
        return self.option is None


def read_fields(command: click.Command) -> tuple[Field, ...]:
    """Reduce a command's parameters to what the form renders.

    `--help` is dropped: click adds it to every command, and offering to fill it in would be
    noise. A parameter the CLI cannot see cannot be rendered, which is the reason commands
    declare every option explicitly rather than taking a passthrough.
    """
    fields: list[Field] = []
    for parameter in command.params:
        if isinstance(parameter, click.Option) and parameter.name == "help":
            continue
        if not isinstance(parameter, click.Option | click.Argument):
            continue
        fields.append(_read_field(parameter))
    return tuple(fields)


def _read_field(parameter: click.Option | click.Argument) -> Field:
    name = parameter.name or ""
    is_flag = isinstance(parameter, click.Option) and bool(parameter.is_flag)
    choices = tuple(getattr(parameter.type, "choices", ()) or ())

    if is_flag:
        kind = FieldKind.FLAG
    elif choices:
        kind = FieldKind.CHOICE
    else:
        kind = FieldKind.VALUE

    return Field(
        name=name,
        label=name.replace("_", " ").capitalize(),
        kind=kind,
        option=parameter.opts[0] if isinstance(parameter, click.Option) else None,
        default=_default_text(parameter, is_flag=is_flag),
        choices=choices,
        help=(getattr(parameter, "help", None) or ""),
        required=bool(parameter.required),
    )


def _default_text(parameter: click.Parameter, *, is_flag: bool) -> str:
    """Render a parameter's default as the text the form starts with."""
    default = parameter.default
    if is_flag:
        return "true" if default else "false"
    if default is None or isinstance(default, bool):
        return ""
    if isinstance(default, str | int | float):
        return str(default)
    return ""


def to_argv(fields: tuple[Field, ...], values: dict[str, str]) -> list[str]:
    """Turn filled-in form values back into the arguments a shell would take.

    The result is shown to the user before anything runs, so the menu teaches the scriptable
    form of whatever they just assembled by hand.
    """
    argv: list[str] = []
    for field in fields:
        value = values.get(field.name, "")
        if field.is_argument():
            if value:
                argv.append(value)
        elif field.kind is FieldKind.FLAG:
            if value == "true":
                argv.append(field.option or "")
        elif value:
            argv.extend([field.option or "", value])
    return argv


def missing_required(
    fields: tuple[Field, ...], values: dict[str, str]
) -> tuple[str, ...]:
    """Return the labels of required fields that are still empty.

    The form's run row stays disabled while this is non-empty, so a command is never invoked
    with an argument click would immediately reject.
    """
    return tuple(
        field.label
        for field in fields
        if field.required
        and field.kind is not FieldKind.FLAG
        and not values.get(field.name, "")
    )


def describe_command(command: click.Command) -> str:
    """The one-line description shown beside a command in the menu.

    Taken from the docstring's first line, which is where click puts it — so documenting a
    command and labelling it in the menu are the same act.
    """
    return command.get_short_help_str(limit=80)


def is_group(command: click.Command) -> bool:
    """Whether selecting this opens a submenu rather than running something."""
    return isinstance(command, click.Group)


def children(group: click.Group) -> tuple[tuple[str, click.Command], ...]:
    """The commands directly under `group`, in the order the menu lists them.

    Alphabetical: a menu whose order depends on import order is a menu whose muscle memory
    breaks whenever someone adds a command.
    """
    return tuple(sorted(group.commands.items()))


def shell_command(prog_name: str, path: tuple[str, ...], argv: list[str]) -> str:
    """Render the equivalent shell invocation, for the confirm screen."""
    parts = [prog_name, *path, *argv]
    return shlex.join([part for part in parts if part])


def context_for(command: click.Command, prog_name: str) -> click.Context:
    """A context suitable for asking click to render help for `command`."""
    return click.Context(command, info_name=prog_name)


def help_text(command: click.Command, prog_name: str) -> str:
    """The full help for `command`, as click would print it."""
    text: Any = context_for(command, prog_name).get_help()
    return str(text)
