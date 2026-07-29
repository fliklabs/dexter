"""Wiring: how a CLI is registered into a container.

The same two shapes every dexter module uses. `use_cli(builder)` registers what the *module*
provides — the registry and the console — and takes no configuration. `register_command(...)`
registers what the *application* contributes, once per command. `use_cli` must run first.

    builder = ContainerBuilder()
    use_cli(builder)
    register_command(builder, storefront, group="example")
    container = builder.build()

    exit_code = await run(container, sys.argv[1:], prog_name="dx")

A command reaches its dependencies through `inject`, which opens a container scope for the
duration of the call — so a command resolves exactly like a request handler does, and whatever
it resolved is released when it finishes.
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Any, Concatenate

import click

from dexter.commons import describe_type
from dexter.dependency_injection import (
    Container,
    ContainerBuilder,
    InvalidRegistrationError,
    Scope,
)

from .console import CliConsole
from .errors import CliNotWiredError
from .tree import CommandTree


def use_cli(builder: ContainerBuilder) -> None:
    """Register the command registry and the shared console. Call once, before any command.

    The registry is bound as an instance so `register_command` can populate it while wiring,
    before the container is built. The console is a singleton: one process, one place output
    goes, which is what lets the interactive layer redirect all of it at once.
    """
    builder.register(CommandTree).to_instance(CommandTree())
    builder.register(CliConsole).to(CliConsole, scope=Scope.SINGLETON)


def register_command(
    builder: ContainerBuilder,
    command: click.Command,
    /,
    *,
    group: str | None = None,
    help: str = "",  # noqa: A002 - matches click's own keyword
) -> None:
    """Add one command to the tree, optionally nested under a named group.

    Args:
        builder: The builder `use_cli` was called on.
        command: A `click.Command`, usually built with the `@click.command` decorator.
        group: Name of the submenu to place it under. Created on first use.
        help: One-line description for that group, used the first time it is created.
    """
    _registry(builder).add(command, group=group, help=help)


def inject[**P, T](
    callback: Callable[Concatenate[Container, P], Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Give an async command callback a container scope as its first argument.

    Apply it closest to the function, under the `click` decorators::

        @click.command("storefront")
        @click.option("--with-failing-reaction", is_flag=True)
        @inject
        async def storefront(scope: Container, with_failing_reaction: bool) -> None: ...

    The scope is closed when the command returns, which now also settles anything registered
    with a `dispose=` — so a command that dispatches work does not have to remember to wait
    for it.
    """

    @functools.wraps(callback)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Awaitable[T]:
        # Synchronous on purpose. click calls this inside its context and then leaves that
        # context before the coroutine it returned is ever awaited — so the container has to
        # be read *now*. An `async def` here would look identical and find no context at all.
        container = _container()
        return _with_scope(container, callback, args, kwargs)

    return wrapper


async def _with_scope[**P, T](
    container: Container,
    callback: Callable[Concatenate[Container, P], Awaitable[T]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> T:
    """Run `callback` inside a scope of `container`, closing it afterwards."""
    async with container.scope() as scope:
        return await callback(scope, *args, **kwargs)


def _container() -> Container:
    """The container `run` put on the click context.

    Asked for silently: outside a click invocation there is no context at all, and click's own
    `RuntimeError` says nothing about what the caller actually did wrong.
    """
    context = click.get_current_context(silent=True)
    container = None if context is None else context.find_object(Container)
    if container is None:
        raise CliNotWiredError(
            "no container is available on the click context. A command using `inject` must "
            "be invoked through `dexter.cli.run`, which is what puts it there."
        )
    return container


def _registry(builder: ContainerBuilder) -> CommandTree:
    """Fetch the registry from the builder, or explain that wiring is missing."""
    try:
        return builder.resolve_instance(CommandTree)
    except InvalidRegistrationError as error:
        raise CliNotWiredError(
            f"{describe_type(CommandTree)} is not registered, so there is nothing to "
            f"register into. Call `use_cli(builder)` before registering commands."
        ) from error
