"""Fixtures and a sample domain local to the CQRS tests.

The domain is deliberately tiny: one command that returns something, one that fails, one that
blocks until released, a query, and an event with several handlers. Everything records into a
`Ledger` so tests can assert on what actually ran rather than on internal state.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import pytest

from dexter.cqrs import (
    Command,
    CommandBus,
    Envelope,
    Event,
    EventBus,
    Next,
    Query,
    use_cqrs,
)
from dexter.dependency_injection import Container, ContainerBuilder


class Ledger:
    """Records what handlers and middleware did, in order."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, entry: str) -> None:
        self.entries.append(entry)


class Gate:
    """Lets a test hold a handler open and release it on demand."""

    def __init__(self) -> None:
        self.opened = asyncio.Event()
        self.arrived = asyncio.Event()

    def release(self) -> None:
        self.opened.set()

    async def wait(self) -> None:
        self.arrived.set()
        await self.opened.wait()


# ── messages ─────────────────────────────────────────────────────────


class CreateUser(Command[int]):
    email: str


class Explode(Command[None]):
    reason: str = "boom"


class Block(Command[int]):
    pass


class Chain(Command[None]):
    """A command whose handler dispatches another command on the same bus."""


class Cascade(Command[None]):
    """A command whose handler publishes an event, which is the central CQRS pattern."""

    pass


class GetUser(Query[str]):
    user_id: int


class CountUsers(Query[int]):
    pass


class UserCreated(Event):
    user_id: int


class NobodyCares(Event):
    pass


# ── handlers ─────────────────────────────────────────────────────────


class CreateUserHandler:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, command: CreateUser) -> int:
        self.ledger.record(f"created {command.email}")
        return 7


class ExplodeHandler:
    async def handle(self, command: Explode) -> None:
        raise RuntimeError(command.reason)


class BlockHandler:
    def __init__(self, gate: Gate, ledger: Ledger) -> None:
        self.gate = gate
        self.ledger = ledger

    async def handle(self, command: Block) -> int:
        await self.gate.wait()
        self.ledger.record("unblocked")
        return 99


class ChainHandler:
    """Dispatches again from inside a handler, so draining has to loop."""

    def __init__(self, commands: CommandBus, ledger: Ledger) -> None:
        self.commands = commands
        self.ledger = ledger

    async def handle(self, command: Chain) -> None:
        await asyncio.sleep(0)
        self.ledger.record("outer ran")
        self.commands.dispatch(CreateUser(email="chained@x.y"))


class CascadeHandler:
    """Publishes an event from inside a command handler.

    The event bus is therefore constructed *during* this handler, which is what makes
    settling the buses in creation order wrong — see `BusGroup`.
    """

    def __init__(self, events: EventBus, ledger: Ledger) -> None:
        self.events = events
        self.ledger = ledger

    async def handle(self, command: Cascade) -> None:
        await asyncio.sleep(0)
        self.ledger.record("command ran")
        self.events.publish(UserCreated(user_id=1))


class GetUserHandler:
    async def handle(self, query: GetUser) -> str:
        return f"user-{query.user_id}"


class CountUsersHandler:
    async def handle(self, query: CountUsers) -> int:
        raise RuntimeError("counting failed")


class RecordFirst:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, event: UserCreated) -> None:
        self.ledger.record(f"first saw {event.user_id}")


class RecordSecond:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, event: UserCreated) -> None:
        self.ledger.record(f"second saw {event.user_id}")


class FailWithValueError:
    async def handle(self, event: UserCreated) -> None:
        raise ValueError("value handler failed")


class FailWithKeyError:
    async def handle(self, event: UserCreated) -> None:
        raise KeyError("key handler failed")


class SlowRecorder:
    """Records that it started, yields, then records that it finished.

    Interleaved entries from two of these are what proves the fan-out is concurrent.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, event: UserCreated) -> None:
        self.ledger.record("slow start")
        await asyncio.sleep(0)
        self.ledger.record("slow end")


class FastRecorder:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, event: UserCreated) -> None:
        self.ledger.record("fast start")
        await asyncio.sleep(0)
        self.ledger.record("fast end")


# ── middleware ───────────────────────────────────────────────────────


class Outer:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        self.ledger.record("outer in")
        result = await call_next(envelope)
        self.ledger.record("outer out")
        return result


class Inner:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        self.ledger.record("inner in")
        result = await call_next(envelope)
        self.ledger.record("inner out")
        return result


class ShortCircuit:
    """Never calls the next link, so no handler ever runs."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        self.ledger.record("short circuited")
        return -1


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def ledger() -> Ledger:
    return Ledger()


@pytest.fixture
def gate() -> Gate:
    return Gate()


@pytest.fixture
def builder(ledger: Ledger, gate: Gate) -> ContainerBuilder:
    """A builder with the CQRS module wired and the test collaborators bound."""
    container_builder = ContainerBuilder()
    container_builder.register(Ledger).to_instance(ledger)
    container_builder.register(Gate).to_instance(gate)
    use_cqrs(container_builder)
    return container_builder


@pytest.fixture
def bare_builder() -> ContainerBuilder:
    """A builder with nothing wired, for asserting what happens without `use_cqrs`."""
    return ContainerBuilder()


@asynccontextmanager
async def running(builder: ContainerBuilder) -> AsyncIterator[Container]:
    """Build the container and yield one scope, closing both afterwards."""
    container = builder.build()
    try:
        async with container.scope() as scope:
            yield scope
    finally:
        await container.aclose()


class Greeter(Protocol):
    """A Protocol, to prove one cannot be registered as a handler."""

    async def handle(self, command: CreateUser) -> int: ...
