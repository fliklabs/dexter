"""The walkthrough. Run with `uv run python -m examples.storefront`.

Each section shows one thing the module does that a signature cannot tell you. Nothing here
asserts anything — read the output and judge it.
"""

import asyncio

from dexter.cqrs import (
    BusGroup,
    CommandBus,
    EventBus,
    QueryBus,
    UnhandledCommandError,
)
from dexter.dependency_injection import Container, DisposalError, ScopeRequiredError

from .display import heading, line, note, short
from .domain import CancelOrder, GetOrder, OrderPlaced, PlaceOrder
from .services import Warehouse
from .wiring import build_container


async def show_a_command_and_its_ticket(container: Container) -> None:
    """Dispatch a command, read its id before the result, then redeem it."""
    heading("a command, and the ticket it hands back")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)

        ticket = commands.dispatch(PlaceOrder(sku="DX-100", quantity=2))
        line(f"id known immediately  -> {short(ticket.id)}")
        line(f"finished yet?         -> {ticket.done()}")

        order_id = await ticket.result()
        line(f"redeemed              -> {order_id.value}")

        note("`order_id` is an OrderId, not Any — PlaceOrder is a Command[OrderId].")
        note("-> and <- lines are the Tracing middleware, wrapping every dispatch.")


async def show_events_fanning_out(container: Container) -> None:
    """Place an order and let its event reach both reactions."""
    heading("one event, two reactions, running concurrently")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)

        ticket = commands.dispatch(PlaceOrder(sku="DX-200", quantity=1))
        await ticket.result()

        # The event the handler published is still in flight here; leaving the scope waits
        # for it, so the reservations below are read after that.
        warehouse = await scope.resolve(Warehouse)
        line(f"warehouse reserved    -> {warehouse.reserved[-1]}")
        line(f"customer emailed      -> {ticket.envelope.message.sku}")
        note("PlaceOrderHandler published OrderPlaced; neither reaction is privileged.")
        note("Both ran before the single <- OrderPlaced line, concurrently.")


async def show_correlation(container: Container) -> None:
    """Show that the event a handler published is stamped with the command that caused it."""
    heading("correlation: the event knows what caused it")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        events = await scope.resolve(EventBus)

        ticket = commands.dispatch(PlaceOrder(sku="DX-300", quantity=5))
        order_id = await ticket.result()

        # Published by the handler, so its envelope is the one `Correlate` supplied.
        follow_up = events.publish(
            OrderPlaced(order_id=order_id.value, sku="DX-300", quantity=5),
            caused_by=ticket.envelope,
        )
        await follow_up.result()

        line(f"command      id={short(ticket.id)}  corr={short(ticket.correlation_id)}")
        line(
            f"event        id={short(follow_up.id)}  "
            f"corr={short(follow_up.correlation_id)}  "
            f"caused_by={short(follow_up.envelope.causation_id or '')}"
        )
        note("Same correlation id, different message ids: one causal chain.")


async def show_a_query(container: Container) -> None:
    """Read something back. A query is answered inline, with no ticket."""
    heading("a query, answered inline")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        queries = await scope.resolve(QueryBus)

        order_id = await commands.dispatch(
            PlaceOrder(sku="DX-400", quantity=3)
        ).result()
        summary = await queries.ask(GetOrder(order_id=order_id.value))
        line(
            f"summary               -> {summary.sku} x{summary.quantity} {summary.status}"
        )

        await commands.dispatch(CancelOrder(order_id=order_id.value)).result()
        cancelled = await queries.ask(GetOrder(order_id=order_id.value))
        line(f"after cancelling      -> {cancelled.status}")
        note("`ask` returns the value; a read has nothing worth deferring.")


async def show_deferred_dispatch(container: Container) -> None:
    """Dispatch several commands without redeeming any of them."""
    heading("deferred: dispatch now, never redeem")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        warehouse = await scope.resolve(Warehouse)
        before = len(warehouse.reserved)

        ids = [
            commands.dispatch(PlaceOrder(sku=f"DX-5{index:02d}", quantity=1)).id
            for index in range(3)
        ]
        for message_id in ids:
            line(f"dispatched, not redeemed  -> {short(message_id)}")

    added = len(warehouse.reserved) - before
    line(f"reserved after leaving    -> {added} more")
    note("None of them was redeemed, and leaving the scope waited for all three.")


async def show_settling(container: Container) -> None:
    """Leave a scope with work still running, and show that it waited."""
    heading("leaving a scope settles its buses")
    warehouse = await container.resolve(Warehouse)
    before = len(warehouse.reserved)

    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        group = await scope.resolve(BusGroup)

        ticket = commands.dispatch(PlaceOrder(sku="DX-700", quantity=4))
        line(f"inside the scope   pending={group.pending}  ticket done={ticket.done()}")

    line(f"after the scope    pending={group.pending}  ticket done={ticket.done()}")
    line(f"reservations added -> {len(warehouse.reserved) - before}")
    note(
        "Nobody called drain(). `use_cqrs` binds BusGroup with dispose=BusGroup.settle,"
    )
    note("so the container settles every bus in the scope on the way out.")
    note("The command ran *and* the event it published reached both reactions —")
    note("the event bus is built inside that handler, so the two settle together.")


async def show_failure_reporting() -> None:
    """Add a reaction that always fails, and show what the others do."""
    heading("when a reaction fails")
    container = build_container(with_failing_reaction=True)
    try:
        warehouse = await container.resolve(Warehouse)
        try:
            async with container.scope() as scope:
                commands = await scope.resolve(CommandBus)
                await commands.dispatch(PlaceOrder(sku="DX-600", quantity=9)).result()
            # Leaving the scope settles its buses, so an unredeemed failure is reported here
            # rather than being silently discarded.
        except DisposalError as error:
            line(f"leaving the scope raised -> {error.args[0]}")
            for failure in error.exceptions:
                for inner in getattr(failure, "exceptions", (failure,)):
                    line(f"  {type(inner).__name__}: {inner}")

        line(f"the others still ran     -> {warehouse.reserved}")
        note("Failures arrive together; one broken reaction silences nothing.")
        note("Nobody called drain(): the container did, on the way out.")
    finally:
        await container.aclose()


async def show_what_goes_wrong(container: Container) -> None:
    """Two mistakes, and what each one reports."""
    heading("what a mistake reports")

    try:
        await container.resolve(CommandBus)
    except ScopeRequiredError:
        line("resolving a bus from the root -> ScopeRequiredError")
        note(
            "A bus must come from a scope, or it would resolve handlers from the root."
        )

    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        try:
            commands.dispatch(Unwired(sku="DX-999", quantity=1))
        except UnhandledCommandError as error:
            line(f"dispatching an unwired command -> {error}")
            note(
                "Raised at the call site, synchronously — there is no ticket to redeem."
            )


class Unwired(PlaceOrder):
    """A command nothing handles, so the transcript can show what that reports."""


SECTIONS = {
    "ticket": show_a_command_and_its_ticket,
    "events": show_events_fanning_out,
    "correlation": show_correlation,
    "query": show_a_query,
    "deferred": show_deferred_dispatch,
    "settling": show_settling,
    "mistakes": show_what_goes_wrong,
}
"""Each part of the walkthrough, so one can be run on its own."""


async def main(*, section: str = "all") -> None:
    """Run the walkthrough, or one section of it.

    Args:
        section: A key of `SECTIONS`, or `"all"` for the whole thing.
    """
    print("dexter · storefront reference app")

    chosen = SECTIONS if section == "all" else {section: SECTIONS[section]}

    container = build_container()
    try:
        for run_section in chosen.values():
            await run_section(container)
    finally:
        await container.aclose()

    if section == "all":
        await show_failure_reporting()

    heading("shutdown")
    line("containers closed")
    print()


if __name__ == "__main__":
    asyncio.run(main())
