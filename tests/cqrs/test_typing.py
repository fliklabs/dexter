"""Static typing guarantees, asserted by running mypy.

Two of this module's promises are type-level and cannot be demonstrated by running anything:
that a dispatch's result is typed by the command rather than being `Any`, and that a handler
registered for the wrong message is an error while you write it. So mypy is run over source
that must fail and source that must pass.

The boundary between what is checked here and what is checked at runtime is the point.
`register_command_handler` pins the *message*, so a handler for a different command is caught
here. It cannot pin the *result*: that needs a type parameter bounded by another type
parameter, which mypy does not support, so the mismatch is caught at registration time instead
and is covered by `test_registration.py`. If this file ever starts passing for the wrong
reason, that split is what to re-check.
"""

import functools
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PRELUDE = '''\
"""Shared declarations for the typing fixtures."""

from dexter.cqrs import Command, CommandBus, Event, EventBus, Query, QueryBus
from dexter.dependency_injection import ContainerBuilder, Scope


class UserId:
    def __init__(self, value: int) -> None:
        self.value = value


class CreateUser(Command[UserId]):
    email: str


class DeleteUser(Command[None]):
    email: str


class GetUser(Query[str]):
    user_id: int


class UserCreated(Event):
    user_id: int


class CreateUserHandler:
    async def handle(self, command: CreateUser) -> UserId:
        return UserId(1)


class DeleteUserHandler:
    async def handle(self, command: DeleteUser) -> None:
        return None


class GetUserHandler:
    async def handle(self, query: GetUser) -> str:
        return "a"


class UserCreatedHandler:
    async def handle(self, event: UserCreated) -> None:
        return None
'''

_BAD_REGISTRATIONS = (
    _PRELUDE
    + """
from dexter.cqrs import (
    register_command_handler,
    register_event_handler,
    register_query_handler,
)

builder = ContainerBuilder()

# Handler for a different command.
register_command_handler(builder, CreateUser, DeleteUserHandler, scope=Scope.TRANSIENT)

# Handler for a different query.
register_query_handler(builder, GetUser, CreateUserHandler, scope=Scope.TRANSIENT)

# Handler for a different event.
register_event_handler(builder, UserCreated, DeleteUserHandler, scope=Scope.TRANSIENT)

# A command where a query is expected.
register_query_handler(builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT)

# Lifetime omitted.
register_command_handler(builder, CreateUser, CreateUserHandler)
"""
)

_BAD_DISPATCH = (
    _PRELUDE
    + """
async def use(commands: CommandBus, queries: QueryBus, events: EventBus) -> None:
    # `CreateUser` produces a `UserId`, not a `str`.
    wrong: str = await commands.dispatch(CreateUser(email="a")).result()

    # `GetUser` produces a `str`, not an `int`.
    also_wrong: int = await queries.ask(GetUser(user_id=1))

    # A command is not an event.
    events.publish(CreateUser(email="a"))

    # A query is answered inline; there is no ticket to redeem.
    queries.ask(GetUser(user_id=1)).result()
"""
)

_GOOD = (
    _PRELUDE
    + """
from dexter.cqrs import (
    register_command_handler,
    register_event_handler,
    register_query_handler,
    use_cqrs,
)

builder = ContainerBuilder()
use_cqrs(builder)
register_command_handler(builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT)
register_command_handler(builder, DeleteUser, DeleteUserHandler, scope=Scope.TRANSIENT)
register_query_handler(builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT)
register_event_handler(builder, UserCreated, UserCreatedHandler, scope=Scope.TRANSIENT)


async def use(commands: CommandBus, queries: QueryBus, events: EventBus) -> str:
    ticket = commands.dispatch(CreateUser(email="a"))
    user_id: UserId = await ticket.result()

    # A `Command[None]` produces nothing, so awaiting the ticket is the whole point.
    await commands.dispatch(DeleteUser(email="a")).result()

    name: str = await queries.ask(GetUser(user_id=user_id.value))

    published = events.publish(UserCreated(user_id=user_id.value))
    await published.result()

    return f"{name}{ticket.id}{published.handler_count}"
"""
)


def run_mypy(source: str) -> subprocess.CompletedProcess[str]:
    """Type-check `source` as a standalone file, from the repository root."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "wiring.py"
        target.write_text(source, encoding="utf-8")
        return subprocess.run(  # noqa: S603 - fixed argv, path from tempfile
            [sys.executable, "-m", "mypy", "--strict", str(target)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )


@functools.cache
def bad_registration_errors() -> str:
    """Run mypy once over the bad registrations and return its output."""
    result = run_mypy(_BAD_REGISTRATIONS)
    assert result.returncode != 0, f"mypy accepted bad registrations:\n{result.stdout}"
    return result.stdout


@functools.cache
def bad_dispatch_errors() -> str:
    """Run mypy once over the bad dispatches and return its output."""
    result = run_mypy(_BAD_DISPATCH)
    assert result.returncode != 0, f"mypy accepted bad dispatches:\n{result.stdout}"
    return result.stdout


class TestHandlersMustMatchTheirMessage:
    def test_rejects_a_handler_for_a_different_command(self) -> None:
        assert "register_command_handler" in bad_registration_errors()

    def test_rejects_a_handler_for_a_different_query(self) -> None:
        assert "register_query_handler" in bad_registration_errors()

    def test_rejects_a_handler_for_a_different_event(self) -> None:
        assert "register_event_handler" in bad_registration_errors()

    def test_rejects_omitting_the_required_scope(self) -> None:
        assert 'Missing named argument "scope"' in bad_registration_errors()

    def test_every_mismatch_is_reported(self) -> None:
        output = bad_registration_errors()
        assert output.count("error:") >= 5


class TestDispatchIsTypedByTheMessage:
    def test_a_command_result_is_not_any(self) -> None:
        """If it were `Any`, assigning it to the wrong type would pass silently."""
        assert 'Incompatible types in assignment (expression has type "UserId"' in (
            bad_dispatch_errors()
        )

    def test_a_query_result_is_not_any(self) -> None:
        """Solved from the annotation, so the mismatch is reported against the query."""
        assert 'incompatible type "GetUser"; expected "Query[int]"' in (
            bad_dispatch_errors()
        )

    def test_a_command_cannot_be_published_as_an_event(self) -> None:
        assert '"publish" of "EventBus" has incompatible type' in bad_dispatch_errors()

    def test_a_query_answer_is_a_value_not_a_ticket(self) -> None:
        assert 'has no attribute "result"' in bad_dispatch_errors()


class TestCorrectWiringTypeChecks:
    def test_a_whole_application_needs_no_suppression(self) -> None:
        result = run_mypy(_GOOD)
        assert result.returncode == 0, result.stdout
