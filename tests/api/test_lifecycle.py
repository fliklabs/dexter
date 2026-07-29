"""One request, one scope — and the guarantee that it closes before the caller is told anything.

This is the property the module is arranged around, and the only one a reader cannot check by
looking at a signature. A scoped service is built once per request and released when that
request ends; anything registered with `dispose=` has finished before the response leaves.

An application that also wires `dexter.cqrs` gets command settling from exactly this, without
`dexter.api` knowing that module exists — the container does the work, and `dispose=` is the
only contract between them. `Settler` below stands in for a bus for that reason.
"""

from http import HTTPMethod, HTTPStatus

import pytest
from pydantic import BaseModel

from dexter.api import (
    HttpExposure,
    RequestContext,
    ResponseCommittedError,
    register_error,
    register_handler,
)
from dexter.dependency_injection import ContainerBuilder, DisposalError, Scope

from .conftest import Ledger, serving


class Settler:
    """Stands in for anything that finishes work as the request scope closes."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def settle(self) -> None:
        self.ledger.record("settled")


class Failing:
    """Something whose release fails, so the request can be seen to fail with it."""

    async def burn(self) -> None:
        raise RuntimeError("could not settle")


class Counter:
    """A scoped service, for counting how often one is built."""

    instances = 0

    def __init__(self) -> None:
        Counter.instances += 1
        self.number = Counter.instances


class Ping(BaseModel):
    pass


class PingHandler:
    """Touch the scoped services and report."""

    def __init__(self, settler: Settler, counter: Counter, ledger: Ledger) -> None:
        self.settler = settler
        self.counter = counter
        self.ledger = ledger

    async def handle(self, request: Ping) -> int:
        self.ledger.record("handled")
        return self.counter.number


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    Counter.instances = 0


@pytest.fixture
def pinging(builder: ContainerBuilder) -> ContainerBuilder:
    """A builder serving `PingHandler`, with a scoped settler and counter."""
    builder.register(Settler).to(Settler, scope=Scope.SCOPED, dispose=Settler.settle)
    builder.register(Counter).to(Counter, scope=Scope.SCOPED)
    register_handler(
        builder,
        PingHandler,
        HttpExposure(method=HTTPMethod.GET, path="/ping"),
        scope=Scope.TRANSIENT,
    )
    return builder


class TestScopePerRequest:
    async def test_a_scoped_service_is_rebuilt_for_each_request(
        self, pinging: ContainerBuilder
    ) -> None:
        async with serving(pinging) as client:
            first = await client.get("/ping")
            second = await client.get("/ping")

        assert first.json() == 1
        assert second.json() == 2

    async def test_one_request_sees_one_instance(
        self, builder: ContainerBuilder
    ) -> None:
        seen: list[Counter] = []

        class Twice:
            def __init__(self, first: Counter, second: Counter) -> None:
                seen.extend((first, second))

            async def handle(self, request: Ping) -> bool:
                return seen[0] is seen[1]

        builder.register(Counter).to(Counter, scope=Scope.SCOPED)
        register_handler(
            builder,
            Twice,
            HttpExposure(method=HTTPMethod.GET, path="/twice"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/twice")

        assert response.json() is True


class TestSettling:
    async def test_disposal_runs_before_the_caller_sees_the_response(
        self, pinging: ContainerBuilder, ledger: Ledger
    ) -> None:
        """The whole point: work the request started has finished before it is answered."""
        async with serving(pinging) as client:
            response = await client.get("/ping")
            ledger.record("client read the response")

        assert ledger.entries == ["handled", "settled", "client read the response"]
        assert response.status_code == HTTPStatus.OK

    async def test_settling_runs_once_per_request(
        self, pinging: ContainerBuilder, ledger: Ledger
    ) -> None:
        async with serving(pinging) as client:
            await client.get("/ping")
            await client.get("/ping")

        assert ledger.entries.count("settled") == 2

    async def test_a_failure_while_settling_reaches_the_request(
        self, builder: ContainerBuilder
    ) -> None:
        """A handler that succeeded but whose work did not settle has not succeeded."""

        class BurnHandler:
            def __init__(self, failing: Failing) -> None:
                self.failing = failing

            async def handle(self, request: Ping) -> str:
                return "ok"

        builder.register(Failing).to(Failing, scope=Scope.SCOPED, dispose=Failing.burn)
        register_handler(
            builder,
            BurnHandler,
            HttpExposure(method=HTTPMethod.GET, path="/burn"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            with pytest.raises(DisposalError):
                await client.get("/burn")

    async def test_a_failure_while_settling_can_be_mapped(
        self, builder: ContainerBuilder
    ) -> None:
        class BurnHandler:
            def __init__(self, failing: Failing) -> None:
                self.failing = failing

            async def handle(self, request: Ping) -> str:
                return "ok"

        builder.register(Failing).to(Failing, scope=Scope.SCOPED, dispose=Failing.burn)
        register_error(builder, DisposalError, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        register_handler(
            builder,
            BurnHandler,
            HttpExposure(method=HTTPMethod.GET, path="/burn"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/burn")

        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


class TestContextLifetime:
    async def test_each_request_gets_its_own_context(
        self, builder: ContainerBuilder
    ) -> None:
        seen: list[RequestContext] = []

        class Recording:
            def __init__(self, context: RequestContext) -> None:
                seen.append(context)

            async def handle(self, request: Ping) -> str:
                return self.__class__.__name__

        register_handler(
            builder,
            Recording,
            HttpExposure(method=HTTPMethod.GET, path="/recording"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            await client.get("/recording", headers={"x-tenant": "a"})
            await client.get("/recording", headers={"x-tenant": "b"})

        assert seen[0] is not seen[1]
        assert [context.headers.get("x-tenant") for context in seen] == ["a", "b"]

    async def test_the_context_is_committed_once_the_request_is_over(
        self, builder: ContainerBuilder
    ) -> None:
        seen: list[RequestContext] = []

        class Recording:
            def __init__(self, context: RequestContext) -> None:
                seen.append(context)

            async def handle(self, request: Ping) -> str:
                return "ok"

        register_handler(
            builder,
            Recording,
            HttpExposure(method=HTTPMethod.GET, path="/recording"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            await client.get("/recording")

        with pytest.raises(ResponseCommittedError):
            seen[0].set_header("x-late", "1")
