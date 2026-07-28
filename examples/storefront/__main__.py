"""The walkthrough. Run with `uv run python -m examples.storefront`.

Each section shows one thing the module does that a signature cannot tell you. Nothing here
asserts anything — read the output and judge it.
"""

import asyncio

from dexter.cqrs import (
    CommandBus,
    DispatchFailedError,
    EventBus,
    QueryBus,
    UnhandledCommandError,
)
from dexter.dependency_injection import Container, ScopeRequiredError

from .display import heading, line, note, short
from .domain import CancelOrder, GetOrder, OrderPlaced, PlaceOrder
from .services import Warehouse
from .wiring import build_container


async def drain(scope: Container) -> None:
    """Wait for every bus in `scope` before leaving it.

    Until the container disposes what it resolved, this is the application's job — and it has
    to happen before the scope exits, or a handler would still be resolving from a closed one.
    """
    for key in (CommandBus, EventBus, QueryBus):
        await (await scope.resolve(key)).drain()


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

        await drain(scope)
        note("`order_id` is an OrderId, not Any — PlaceOrder is a Command[OrderId].")
        note("-> and <- lines are the Tracing middleware, wrapping every dispatch.")


async def show_events_fanning_out(container: Container) -> None:
    """Place an order and let its event reach both reactions."""
    heading("one event, two reactions, running concurrently")
    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        events = await scope.resolve(EventBus)

        ticket = commands.dispatch(PlaceOrder(sku="DX-200", quantity=1))
        await ticket.result()
        await events.drain()

        warehouse = await scope.resolve(Warehouse)
        line(f"warehouse reserved    -> {warehouse.reserved[-1]}")
        line(f"customer emailed      -> {ticket.envelope.message.sku}")
        note("PlaceOrderHandler published OrderPlaced; neither reaction is privileged.")
        note("Both ran before the single <- OrderPlaced line, concurrently.")

        await drain(scope)


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

        await drain(scope)


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

        await drain(scope)


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

        await drain(scope)

        added = len(warehouse.reserved) - before
        line(f"reserved after draining   -> {added} more")
        note("`drain()` waits for work nobody is holding a ticket for.")


async def show_failure_reporting() -> None:
    """Add a reaction that always fails, and show what the others do."""
    heading("when a reaction fails")
    container = build_container(with_failing_reaction=True)
    try:
        async with container.scope() as scope:
            commands = await scope.resolve(CommandBus)
            events = await scope.resolve(EventBus)

            await commands.dispatch(PlaceOrder(sku="DX-600", quantity=9)).result()

            try:
                await events.drain()
            except DispatchFailedError as error:
                line(f"drain reported        -> {error.args[0]}")
                for failure in error.exceptions:
                    for inner in getattr(failure, "exceptions", (failure,)):
                        line(f"  {type(inner).__name__}: {inner}")

            warehouse = await scope.resolve(Warehouse)
            line(f"the others still ran  -> {warehouse.reserved}")
            note("Failures arrive together; one broken reaction silences nothing.")

            await commands.drain()
            await (await scope.resolve(QueryBus)).drain()
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


async def main() -> None:
    """Run the whole walkthrough."""
    print("dexter · storefront reference app")

    container = build_container()
    try:
        await show_a_command_and_its_ticket(container)
        await show_events_fanning_out(container)
        await show_correlation(container)
        await show_a_query(container)
        await show_deferred_dispatch(container)
        await show_what_goes_wrong(container)
    finally:
        await container.aclose()

    await show_failure_reporting()

    heading("shutdown")
    line("containers closed")
    print()


if __name__ == "__main__":
    asyncio.run(main())
