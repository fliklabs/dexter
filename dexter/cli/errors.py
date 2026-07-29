"""Exceptions raised by the CLI module.

A CLI is a place where an exception usually means "print something and set an exit code", not
"unwind the process". Everything here is therefore narrow enough to be caught and rendered:
`dexter.cli` never lets a failure tear down a terminal it has put into raw mode.
"""

from dexter.commons import DexterError, describe_type


class CliError(DexterError):
    """Base class for every CLI failure."""


# ── registration ─────────────────────────────────────────────────────


class CliRegistrationError(CliError):
    """A command could not be registered. Raised while wiring."""


class CliNotWiredError(CliRegistrationError):
    """`use_cli` was never called on this builder.

    The registry a command registers into is created by `use_cli`, so it has to run first.
    """


class DuplicateCommandError(CliRegistrationError):
    """A command with that name is already registered at that level.

    Rebinding is not silently permitted: which one wins would depend on the order unrelated
    wiring happened to run in, and the loser would simply vanish from the menu.
    """


class InvalidCommandError(CliRegistrationError):
    """The command cannot be used.

    Covers anything that is not a `click.Command`, and a command with no name — the name is
    what a user types and what the menu shows, so there is nothing to do without one.
    """

    def __init__(self, command: object, reason: str) -> None:
        """Name the offending command and say what is wrong with it."""
        super().__init__(f"cannot register {describe_type(type(command))}: {reason}")
        self.command = command


# ── running ──────────────────────────────────────────────────────────


class InteractiveUnavailableError(CliError):
    """The menu cannot be shown.

    `curses` is in the standard library but not on every platform, and a terminal is required
    besides. Raised instead of letting an `ImportError` for a stdlib module reach the user,
    which reads like a broken installation rather than an unsupported environment.
    """
