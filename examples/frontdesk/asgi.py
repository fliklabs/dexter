"""Calling an ASGI application directly, with no socket and no server.

This is the whole point of `create_app` returning an application rather than starting one:
everything below is `await app(scope, receive, send)`. A real deployment hands the same object
to a server; this walkthrough hands it three dictionaries.

Written by hand rather than with an HTTP client library so that the example depends on nothing
a consumer would not already have, and so that the request a handler sees — headers and all —
is visible in the source rather than hidden behind a client.
"""

import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any
from urllib.parse import urlencode

type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type Application = Callable[[Message, Receive, Send], Awaitable[None]]
"""The ASGI contract, spelled out. A `FastAPI` instance satisfies it, and so would any other
application object — which is the point of `create_app` handing one back."""


class Reply:
    """What came back: the status, the headers, and the decoded body."""

    __slots__ = ("body", "headers", "status")

    def __init__(
        self, status: int, headers: list[tuple[str, str]], body: bytes
    ) -> None:
        """Record one response."""
        self.status = status
        self.headers = headers
        self.body: Any = json.loads(body) if body else None
        """The decoded body. Deliberately `Any`: a walkthrough reads whatever came back."""

    def header(self, name: str) -> str | None:
        """The first value of `name`, or `None`. Case-insensitive."""
        wanted = name.lower()
        return next((value for key, value in self.headers if key == wanted), None)

    def __repr__(self) -> str:
        return f"Reply(status={self.status}, body={self.body!r})"


async def call(  # noqa: PLR0913 - a request; every one of these is a distinct fact
    app: Application,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: object | None = None,
) -> Reply:
    """Call `app` once and return what it sent back.

    Args:
        app: The ASGI application `create_app` returned.
        method: The HTTP method.
        path: The path, without a query string.
        query: Query parameters, encoded into the request.
        headers: Request headers. A JSON content type is added when there is a body.
        body: An object to send as a JSON body, or `None` to send nothing.
    """
    payload = b"" if body is None else json.dumps(body).encode()
    sent = dict(headers or {})
    if payload:
        sent.setdefault("content-type", "application/json")
        sent.setdefault("content-length", str(len(payload)))

    scope: Message = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(dict(query or {})).encode(),
        "root_path": "",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in sent.items()
        ],
        "client": ("203.0.113.7", 54321),
        "server": ("frontdesk", 80),
        "state": {},
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": payload, "more_body": False}

    status = 500
    response_headers: list[tuple[str, str]] = []
    chunks: list[bytes] = []

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
            response_headers.extend(
                (key.decode().lower(), value.decode())
                for key, value in message.get("headers", ())
            )
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))

    await app(scope, receive, send)
    return Reply(status, response_headers, b"".join(chunks))
