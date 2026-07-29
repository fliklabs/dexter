"""Where every command is registered. This is the file to read first.

The shape is the convention every dexter module follows: `use_cli` registers what the *module*
provides, then one `register_command` per thing this repository contributes. Adding a command
is writing it under `commands/` and adding a line here — the menu needs no other change,
because the menu is generated from this same tree.
"""

from dexter.cli import register_command, use_cli
from dexter.dependency_injection import Container, ContainerBuilder

from .commands.checks import test, verify
from .commands.example import frontdesk, list_examples, storefront, taskflow
from .commands.serve import serve


def build_container() -> Container:
    """Wire the CLI and return a container ready to run it."""
    builder = ContainerBuilder()
    use_cli(builder)

    register_command(
        builder, list_examples, group="example", help="Run a reference application."
    )
    register_command(builder, taskflow, group="example")
    register_command(builder, storefront, group="example")
    register_command(builder, frontdesk, group="example")

    register_command(builder, serve)
    register_command(builder, test)
    register_command(builder, verify)

    return builder.build()
