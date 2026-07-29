"""The walkthrough. Run with `uv run python -m examples.frontdesk`.

Each section shows one thing the module does that a signature cannot tell you. Nothing here
asserts anything — read the output and judge it.

**No server is started and no port is bound.** `create_app` returns an ASGI application, and
`asgi.call` invokes it directly. That is the same object a real deployment would hand to a
server; here the walkthrough hands it three dictionaries and reads what comes back.
"""

import asyncio
import json

from dexter.api.http import create_app
from dexter.dependency_injection import Container

from .asgi import Application, call
from .display import heading, line, note, reply, request
from .services import AuditTrail, Housekeeping
from .wiring import build_container

TENANT = {"x-tenant": "acme", "user-agent": "frontdesk-demo/1.0"}
"""The headers a well-behaved caller sends. Every section but `middleware` uses them."""


async def show_routing(app: Application, container: Container) -> None:  # noqa: ARG001
    """Read a path parameter, a query string, and a body."""
    heading("one request model, filled from wherever the path says")

    request("GET", "/rooms", "?floor=2&limit=2")
    answer = await call(
        app, "GET", "/rooms", query={"floor": "2", "limit": "2"}, headers=TENANT
    )
    reply(answer.status, answer.body)
    note("Every field of SearchRoomsRequest came from the query string.")

    request("POST", "/bookings", '{"room": "101", "nights": 3}')
    answer = await call(
        app, "POST", "/bookings", headers=TENANT, body={"room": "101", "nights": 3}
    )
    reply(answer.status, answer.body, f"location: {answer.header('location')}")
    note("The body became BookRoomRequest; the handler set the status and the header.")

    reference = str(answer.body["reference"])
    request("GET", f"/bookings/{reference}")
    answer = await call(app, "GET", f"/bookings/{reference}", headers=TENANT)
    reply(answer.status, answer.body)
    note("`reference` came from the path, and nothing else was needed.")


async def show_headers(app: Application, container: Container) -> None:  # noqa: ARG001
    """Read the headers, the cookie and the address the caller arrived with."""
    heading("what the handler can see about its caller")

    request("GET", "/whoami", "with a header and a cookie")
    answer = await call(
        app,
        "GET",
        "/whoami",
        headers={**TENANT, "cookie": "session=s-42"},
    )
    reply(answer.status, answer.body)
    note("None of this is in the request model. WhoamiApi asked for a RequestContext,")
    note("and so could a repository three levels beneath it — that is the whole reason")
    note("the context is injected rather than handed to `handle` as a second argument.")


async def show_identity(app: Application, container: Container) -> None:
    """Show a service receiving the caller's tenant without ever mentioning HTTP."""
    heading("identity, injected rather than threaded through")

    for tenant in ("acme", "globex"):
        await call(
            app,
            "POST",
            "/bookings",
            headers={"x-tenant": tenant},
            body={"room": "201" if tenant == "acme" else "202", "nights": 1},
        )

    trail = await container.resolve(AuditTrail)
    for entry in trail.entries:
        line(entry)
    note("`Audit` never reads a header. It declares `tenant: Tenant` and the container")
    note("supplies the right one, because `current_tenant` is bound Scope.SCOPED.")
    note("A ContextVar carries it, so requests sharing the loop thread stay separate.")


async def show_middleware(app: Application, container: Container) -> None:  # noqa: ARG001
    """Refuse a request before the handler is ever built."""
    heading("middleware, resolved from the request's own scope")

    request("GET", "/whoami", "no X-Tenant header")
    answer = await call(app, "GET", "/whoami")
    reply(answer.status, answer.body)
    note(
        "RequireTenant returned instead of calling `call_next`, so WhoamiApi never ran"
    )
    note("and was never even constructed — `Invocation.handler` is the class, and the")
    note("container is only asked for an instance if the pipeline reaches the end.")
    note(
        "Trace still printed both halves: it is registered first, so it wraps the refusal."
    )


async def show_errors(app: Application, container: Container) -> None:  # noqa: ARG001
    """Map a domain failure to a status, and leave an unmapped one alone."""
    heading("a domain failure, and what a caller is told")

    request("GET", "/bookings/BK-999")
    answer = await call(app, "GET", "/bookings/BK-999", headers=TENANT)
    reply(answer.status, answer.body, f"as {answer.header('content-type')}")
    note("NoSuchBookingError was mapped to 404. The body is RFC 9457 problem details.")

    request("POST", "/bookings", '{"room": "101", ...}  already booked')
    answer = await call(
        app, "POST", "/bookings", headers=TENANT, body={"room": "101", "nights": 1}
    )
    reply(answer.status, answer.body)

    request("POST", "/bookings", '{"room": "101", "nights": 99}')
    answer = await call(
        app, "POST", "/bookings", headers=TENANT, body={"room": "101", "nights": 99}
    )
    reply(answer.status, answer.body)
    note("This one never reached a handler: `nights` is declared `le=30`, so the")
    note("framework refused it and no request scope was ever opened.")


async def show_cqrs(app: Application, container: Container) -> None:
    """Dispatch a command from a handler and never redeem what it caused."""
    heading("the request scope settles before the caller is answered")

    housekeeping = await container.resolve(Housekeeping)
    before = len(housekeeping.pending)

    request("POST", "/bookings", '{"room": "301", "nights": 2}')
    answer = await call(
        app, "POST", "/bookings", headers=TENANT, body={"room": "301", "nights": 2}
    )
    reply(answer.status, answer.body)

    line(f"housekeeping, read straight after -> {housekeeping.pending[before:]}")
    note("BookRoomApi dispatched a command and awaited only its result. The event that")
    note("command's handler published was still in flight when the handler returned.")
    note(
        "Leaving the request scope settled the buses — so by the time the response was"
    )
    note(
        "built, NotifyHousekeeping had already run. dexter.api never imports dexter.cqrs;"
    )
    note("the container does this, because `use_cqrs` binds a `dispose=`.")


async def show_schema(app: Application, container: Container) -> None:  # noqa: ARG001
    """Show the generated schema, which is the other half of using a real framework."""
    heading("the schema, generated from the same declarations")

    answer = await call(app, "GET", "/openapi.json")
    schema = answer.body
    operation = schema["paths"]["/bookings/{reference}"]["get"]

    line(f"summary   -> {operation['description']}")
    line(f"tags      -> {operation['tags']}")
    line(f"params    -> {[(p['name'], p['in']) for p in operation['parameters']]}")

    nights = schema["components"]["schemas"]["BookRoomRequest"]["properties"]["nights"]
    line(f"nights    -> {json.dumps(nights, separators=(', ', ': '))}")
    note(
        "The constraint and the description came off the pydantic field, not a decorator."
    )


SECTIONS = {
    "routing": show_routing,
    "headers": show_headers,
    "identity": show_identity,
    "middleware": show_middleware,
    "errors": show_errors,
    "cqrs": show_cqrs,
    "schema": show_schema,
}
"""Each part of the walkthrough, so one can be run on its own."""


async def main(*, section: str = "all") -> None:
    """Run the walkthrough, or one section of it.

    Args:
        section: A key of `SECTIONS`, or `"all"` for the whole thing.
    """
    print("dexter · frontdesk reference app")

    chosen = SECTIONS if section == "all" else {section: SECTIONS[section]}

    container = build_container()
    try:
        # Returns an application. Nothing is listening, and nothing has been started.
        app: Application = await create_app(container)
        for run_section in chosen.values():
            await run_section(app, container)
    finally:
        await container.aclose()

    heading("shutdown")
    line("container closed")
    print()


if __name__ == "__main__":
    asyncio.run(main())
