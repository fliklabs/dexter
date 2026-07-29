"""The service, run as a worker. `uv run python -m examples.storefront`.

The same modules `./dx serve` puts behind a socket, reached here through the buses instead —
which is the point of a module declaring what it does rather than how it is called. Nothing
here starts a server or binds a port.

Each section shows one thing a signature cannot tell you. Nothing asserts anything: read the
output and judge it.
"""

import asyncio

from dexter.application import ModuleRegistry, register_module, use_application
from dexter.cqrs import CommandBus, QueryBus, UnhandledCommandError
from dexter.dependency_injection import (
    Container,
    ContainerBuilder,
    ResolutionError,
    ScopeRequiredError,
)

from .application import build_container
from .display import heading, line, note, short
from .modules.catalogue.domain import Catalogue
from .modules.orders.domain import (
    Dispatches,
    GetOrder,
    OutOfStockError,
    PlaceOrder,
)
from .modules.orders.handlers import PlaceOrderHandler
from .modules.orders.use import use_orders


async def show_what_it_is_made_of(container: Container) -> None:
    """Ask the application what modules it has."""
    heading("what this service is made of")
    registry = await container.resolve(ModuleRegistry)
    for name in registry.names():
        line(name)
    note("`MODULES` in application.py is the only place this is written down.")
    note("Adding a capability is a module package and one more line in that list.")


async def show_a_command_and_its_ticket(container: Container) -> None:
    """Place an order, read the ticket before the result, then redeem it."""
    heading("a command, and the ticket it hands back")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)

        ticket = commands.dispatch(PlaceOrder(sku="DX-100", quantity=2))
        line(f"id known immediately  -> {short(ticket.id)}")
        line(f"finished yet?         -> {ticket.done()}")

        reference = await ticket.result()
        line(f"redeemed              -> {reference}")
        note("`reference` is a str, not Any — PlaceOrder is a Command[str].")


async def show_modules_composing(container: Container) -> None:
    """Show orders pricing against the catalogue without importing it."""
    heading("one module using another, through a contract")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        queries = await scope.resolve(QueryBus)

        reference = await commands.dispatch(
            PlaceOrder(sku="DX-200", quantity=3)
        ).result()
        order = await queries.ask(GetOrder(reference=reference))

        line(f"ordered   -> {order.quantity} x {order.sku}")
        line(f"priced at -> {order.pence}p, read from the catalogue when placed")

        remaining = await (await scope.resolve(Catalogue)).list(in_stock_only=False)
        taken = next(product for product in remaining if product.sku == "DX-200")
        line(f"stock now -> {taken.in_stock}")
        note("PlaceOrderHandler asked for `Catalogue`, the contract the other module")
        note(
            "declared. It imports no service of that module and names no module at all."
        )


async def show_the_event_settling(container: Container) -> None:
    """Leave a scope with a reaction still in flight, and show that it waited."""
    heading("leaving a scope settles what was dispatched in it")
    dispatches = await container.resolve(Dispatches)
    before = len(dispatches.pending())

    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        ticket = commands.dispatch(PlaceOrder(sku="DX-100", quantity=1))
        await ticket.result()
        line(f"inside the scope  -> queued {len(dispatches.pending()) - before}")

    line(f"after the scope   -> queued {len(dispatches.pending()) - before}")
    note(
        "Nobody awaited the reaction. `use_cqrs` binds the bus group with a `dispose=`,"
    )
    note("so leaving the scope settled it — which is also why an HTTP response is not")
    note("built until the work its request started has finished.")


async def show_failures(container: Container) -> None:
    """Two mistakes and one refusal, and what each reports."""
    heading("what goes wrong, and what it says")

    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        try:
            await commands.dispatch(PlaceOrder(sku="DX-300", quantity=1)).result()
        except OutOfStockError as error:
            line(f"ordering something sold out -> {error}")
            note("Mapped to 409 for a caller; here it is just the exception.")

        try:
            commands.dispatch(Unwired(sku="DX-100", quantity=1))
        except UnhandledCommandError as error:
            line(f"dispatching an unwired command -> {error}")
            note(
                "Raised at the call site, synchronously — there is no ticket to redeem."
            )

    try:
        await container.resolve(CommandBus)
    except ScopeRequiredError:
        line("resolving a bus from the root -> ScopeRequiredError")
        note(
            "A bus must come from a scope, or it would resolve handlers from the root."
        )


async def show_a_missing_module(container: Container) -> None:  # noqa: ARG001
    """Build a service missing a module that something else depends on.

    `container` is unused: this section deliberately builds a *different*, incomplete one.
    """
    heading("a module left out")

    builder = ContainerBuilder()
    use_application(builder)
    register_module(builder, use_orders)  # and deliberately not `use_catalogue`
    incomplete = builder.build()

    try:
        async with incomplete.scope() as scope:
            await scope.resolve(PlaceOrderHandler)
    except ResolutionError as error:
        line("resolving the order handler ->")
        for text in str(error).splitlines():
            line(f"  {text}")
        note(
            "Nothing checks module dependencies when they are registered. The container"
        )
        note(
            "reports it where it is actually needed, naming what was looked for and the"
        )
        note("chain that led there — which is more use than a list of declared names.")
    finally:
        await incomplete.aclose()


class Unwired(PlaceOrder):
    """A command nothing handles, so the transcript can show what that reports."""


SECTIONS = {
    "modules": show_what_it_is_made_of,
    "ticket": show_a_command_and_its_ticket,
    "composition": show_modules_composing,
    "settling": show_the_event_settling,
    "failures": show_failures,
    "missing": show_a_missing_module,
}
"""Each part of the walkthrough, so one can be run on its own."""


async def main(*, section: str = "all") -> None:
    """Run the walkthrough, or one section of it.

    Args:
        section: A key of `SECTIONS`, or `"all"` for the whole thing.
    """
    print("dexter · storefront reference service (worker)")

    chosen = SECTIONS if section == "all" else {section: SECTIONS[section]}

    container = build_container()
    try:
        for run_section in chosen.values():
            await run_section(container)
    finally:
        await container.aclose()

    heading("shutdown")
    line("container closed")
    print()


if __name__ == "__main__":
    asyncio.run(main())
