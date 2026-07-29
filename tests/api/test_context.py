"""What a handler can see about the request, and what it can say about the response.

This file is the regression suite for the limitation the module exists to remove: a handler
that cannot reach the headers and cookies its caller sent. Every test here would have been
impossible to write against a design that rebuilt a narrow request model and dropped the
transport's own request before the handler ran.

`TestIsolation` is the load-bearing one. A `threading.local` passes every other test in this
file and fails that one, because concurrent requests share the event loop's thread.
"""

import asyncio
from http import HTTPStatus

import pytest

from dexter.api import (
    Cookie,
    Headers,
    NoRequestContextError,
    QueryValues,
    RequestContext,
    ResponseCommittedError,
    bind_request,
    current_request,
)
from dexter.dependency_injection import (
    CaptiveDependencyError,
    ContainerBuilder,
    Scope,
    ScopeRequiredError,
)

from .conftest import GetRoomHandler, make_context, running


class TestHeaders:
    def test_lookup_is_case_insensitive(self) -> None:
        headers = Headers([("X-Tenant", "acme")])
        assert headers.get("x-tenant") == "acme"
        assert headers.get("X-TENANT") == "acme"
        assert "X-Tenant" in headers

    def test_keeps_every_value_for_a_repeated_name(self) -> None:
        headers = Headers([("accept", "text/html"), ("accept", "application/json")])
        assert headers["accept"] == "text/html"
        assert headers.get_all("Accept") == ("text/html", "application/json")

    def test_reports_nothing_for_an_absent_name(self) -> None:
        headers = Headers()
        assert headers.get("x-missing") is None
        assert headers.get_all("x-missing") == ()
        assert len(headers) == 0


class TestQueryValues:
    def test_lookup_is_case_sensitive(self) -> None:
        query = QueryValues([("Tag", "a"), ("tag", "b")])
        assert query["Tag"] == "a"
        assert query["tag"] == "b"

    def test_keeps_every_value_for_a_repeated_name(self) -> None:
        query = QueryValues([("tag", "a"), ("tag", "b")])
        assert query.get_all("tag") == ("a", "b")
        assert list(query) == ["tag"]


class TestBinding:
    async def test_a_bound_context_is_the_current_one(self) -> None:
        context = make_context()
        with bind_request(context):
            assert current_request() is context

    def test_raises_when_nothing_is_bound(self) -> None:
        with pytest.raises(NoRequestContextError, match="no request is bound"):
            current_request()

    def test_restores_whatever_was_bound_before(self) -> None:
        outer = make_context()
        inner = make_context()
        with bind_request(outer):
            with bind_request(inner):
                assert current_request() is inner
            assert current_request() is outer

    def test_leaving_the_block_unbinds(self) -> None:
        with bind_request(make_context()):
            pass
        with pytest.raises(NoRequestContextError):
            current_request()


class TestIsolation:
    async def test_concurrent_requests_never_see_each_others_context(self) -> None:
        """Two requests on one loop thread. A `threading.local` fails this."""

        async def serve(tenant: str) -> str:
            context = make_context(headers=Headers([("x-tenant", tenant)]))
            with bind_request(context):
                # Yield the loop, so the other request runs on this same thread in between.
                await asyncio.sleep(0)
                return current_request().headers.get("x-tenant") or ""

        assert list(await asyncio.gather(serve("a"), serve("b"))) == ["a", "b"]

    async def test_a_task_started_inside_a_request_inherits_it(self) -> None:
        context = make_context()
        with bind_request(context):
            assert await asyncio.create_task(_seen()) is context

    async def test_a_task_started_outside_a_request_sees_none(self) -> None:
        with pytest.raises(NoRequestContextError):
            await asyncio.create_task(_seen())


async def _seen() -> RequestContext:
    """The context visible to a freshly created task."""
    return current_request()


class TestResolution:
    async def test_a_scoped_resolve_finds_the_bound_request(
        self, builder: ContainerBuilder
    ) -> None:
        context = make_context()
        with bind_request(context):
            async with running(builder) as scope:
                assert await scope.resolve(RequestContext) is context

    async def test_resolving_from_the_root_is_refused(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            with bind_request(make_context()), pytest.raises(ScopeRequiredError):
                await container.resolve(RequestContext)
        finally:
            await container.aclose()

    async def test_a_singleton_may_not_depend_on_it(
        self, builder: ContainerBuilder
    ) -> None:
        """A process-wide service cannot capture one request's caller. Caught at build()."""

        class Tracker:
            def __init__(self, context: RequestContext) -> None:
                self.context = context

        builder.register(Tracker).to(Tracker, scope=Scope.SINGLETON)
        with pytest.raises(CaptiveDependencyError):
            builder.build()

    async def test_one_request_yields_one_context(
        self, builder: ContainerBuilder
    ) -> None:
        with bind_request(make_context()):
            async with running(builder) as scope:
                first = await scope.resolve(RequestContext)
                second = await scope.resolve(RequestContext)
                assert first is second


class TestResponseDirectives:
    def test_records_a_status_a_header_and_a_cookie(self) -> None:
        context = make_context()
        context.set_status(HTTPStatus.CREATED)
        context.set_header("location", "/rooms/1")
        context.set_cookie(Cookie(name="session", value="abc"))

        assert context.status is HTTPStatus.CREATED
        assert context.response_headers() == (("location", "/rooms/1"),)
        assert context.response_cookies()[0].name == "session"

    def test_repeating_a_header_adds_another_value(self) -> None:
        context = make_context()
        context.set_header("set-cookie", "a=1")
        context.set_header("set-cookie", "b=2")
        assert len(context.response_headers()) == 2

    def test_status_is_none_until_one_is_asked_for(self) -> None:
        assert make_context().status is None

    def test_raises_when_changed_after_the_response_was_built(self) -> None:
        context = make_context()
        context.commit()
        with pytest.raises(ResponseCommittedError, match="already been built"):
            context.set_header("x-late", "1")

    def test_leaving_the_binding_commits_it(self) -> None:
        context = make_context()
        with bind_request(context):
            context.set_header("x-fine", "1")
        with pytest.raises(ResponseCommittedError):
            context.set_status(HTTPStatus.OK)

    def test_committing_twice_is_harmless(self) -> None:
        context = make_context()
        context.commit()
        context.commit()


class TestDescription:
    def test_names_the_handler_it_is_serving(self) -> None:
        assert make_context().handler is GetRoomHandler

    def test_reads_as_the_operation_and_path(self) -> None:
        assert "GetRoomHandler" in repr(make_context())

    def test_defaults_are_empty_rather_than_absent(self) -> None:
        context = RequestContext(handler=GetRoomHandler)
        assert context.headers.get("any") is None
        assert context.cookies == {}
        assert context.query.get_all("any") == ()
        assert context.path_params == {}
        assert context.client_host is None
        assert context.state == {}
