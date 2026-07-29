"""Serving the reference application.

The service is otherwise only readable as a printed transcript. This puts it behind a real
socket so it can be poked from a browser — which is the only way to see what a schema, a status
code or a per-request scope actually does to a caller.

**The same container the worker builds.** `examples.storefront.application.build_container` is
called here exactly as `python -m examples.storefront` calls it; the only difference is what
happens next. That is what a module list buys: one description of what the service is, and two
ways to run it.

This is the only thing in the repository that binds a port, which is why it lives here rather
than in `examples/`: an example is smoke-run by CI with no timeout, and a server would hang it.
"""

import asyncio
import socket
import time
import webbrowser
from collections.abc import Awaitable, Callable

import click
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from dexter.api.http import create_app
from dexter.application import ModuleRegistry
from dexter.cli import CliConsole, inject
from dexter.dependency_injection import Container
from examples.storefront.application import build_container


@click.command("serve")
@click.option("--host", default="127.0.0.1", help="Address to bind.")
@click.option("--port", default=8000, type=int, help="Port to bind.")
@click.option("--open", "open_browser", is_flag=True, help="Open a browser on start.")
@inject
async def serve(scope: Container, host: str, port: int, *, open_browser: bool) -> int:
    """Serve every reference application at once, for poking from a browser.

    `scope` is this command's own container, and supplies only the console. Each example
    builds its own, which is the point — they have to stand alone as something a reader can
    copy, and two of them use CQRS so they could not share a builder anyway.
    """
    console = await scope.resolve(CliConsole)
    container = build_container()
    try:
        app = await _compose(container)
        modules = (await container.resolve(ModuleRegistry)).names()
        return await _serve(
            app, host, port, console, modules, open_browser=open_browser
        )
    finally:
        await container.aclose()


async def _compose(container: Container) -> FastAPI:
    """Put every module's routes onto one application."""
    app = FastAPI(
        title="dexter reference service",
        description="A storefront, composed from modules.",
    )
    await create_app(container, app=app)

    app.middleware("http")(_log_requests)
    app.get("/", include_in_schema=False)(_index)
    return app


async def _serve(  # noqa: PLR0913 - a server; every one of these is a distinct fact
    app: FastAPI,
    host: str,
    port: int,
    console: CliConsole,
    modules: tuple[str, ...],
    *,
    open_browser: bool,
) -> int:
    """Run the server until it is stopped, and stop it cleanly when it is."""
    try:
        sock = _bind(host, port)
    except OSError as error:
        # Bound here rather than left to the server, which reports this by logging the raw
        # errno and calling `sys.exit` — so nothing above could turn it into a readable
        # message, and the address would already have been announced as though it worked.
        console.error(f"cannot serve on {host}:{port} — {error.strerror or error}")
        console.detail("something is already listening there. Try `--port`.")
        return 1

    config = uvicorn.Config(app, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    # The server installs its own SIGINT handler while it runs, and it is left alone — the two
    # ways of stopping it do not overlap. From a shell, Ctrl+C raises SIGINT and the server
    # shuts itself down. From the menu there is no signal to handle at all: curses raw mode
    # delivers Ctrl+C as a byte, so stopping is the navigator cancelling this command, which
    # arrives below as cancellation rather than as a signal.

    _announce(console, host, port, modules)
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/docs")

    running = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        # Shielded, so being told to stop does not *cancel* the server. Cancellation tears
        # through whatever it is awaiting — including the lifespan, which then reports its own
        # interruption as an unhandled error. Asking it to exit and waiting is the difference
        # between a clean stop and a traceback on the way out.
        await asyncio.shield(running)
    except asyncio.CancelledError, KeyboardInterrupt:
        server.should_exit = True
        await running
        console.detail("server stopped")
        raise
    finally:
        sock.close()
    return 0


def _bind(host: str, port: int) -> socket.socket:
    """Take the address before anything is announced, so a clash is reported as one.

    Bound but not listening: `loop.create_server` does that itself, and doing it twice is a
    difference the reader should not have to know about.
    """
    sock = socket.socket(family=socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def _announce(
    console: CliConsole, host: str, port: int, modules: tuple[str, ...]
) -> None:
    """Say where everything is, and what is being served, before anything is listening."""
    base = f"http://{host}:{port}"
    console.heading("serving")
    table = console.table("What", "Where")
    table.add_row("[cyan]everything[/]", f"{base}/docs")
    table.add_row("[cyan]index[/]", base)
    console.print(table)
    console.detail(f"modules: {', '.join(modules)}")
    console.detail("Ctrl+C to stop")


async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Print one line per request, which the menu paints into its pane as it arrives."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    path = request.url.path
    # Flushed, because a log nobody sees until the server stops is not a log. Redirected to a
    # file or a pipe, stdout buffers ~8KB by default and a request every few seconds would
    # take minutes to appear. In the menu the stream is a buffer in memory, where flushing
    # costs nothing.
    print(
        f"  {request.method:<6} {path:<34} {response.status_code}  {elapsed:.0f}ms",
        flush=True,
    )
    return response


async def _index() -> HTMLResponse:
    """A plain page pointing at the docs, so a browser has somewhere to start."""
    return HTMLResponse(
        "<h1>dexter reference service</h1>"
        "<p>A storefront, composed from modules. Send requests from the "
        '<a href="/docs">API docs</a> — every module\'s routes are there, grouped by '
        "the tag it registered them under.</p>"
    )
