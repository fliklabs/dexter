"""One request, and everything true about *this invocation* rather than about its input.

This is the module's answer to the question a request model cannot answer: what did the
caller send *around* the payload, and what should go back around the response. Headers,
cookies, the caller's address, and a place for middleware to leave something a handler will
read — none of which belongs in a typed request model, and all of which is unreachable if the
transport's own request object is thrown away before the handler runs.

**A handler reaches it by asking for it.** `use_api` binds `RequestContext` as
`Scope.SCOPED`, so a handler, a middleware, or a service three levels below either one
declares an ordinary constructor parameter and the container supplies the one belonging to
the request being served. That matters more than it first appears: the thing that wants the
caller's identity is rarely the handler. It is a repository wanting the tenant, an audit
service wanting the address, a policy object wanting a token — and threading a parameter down
to each of them by hand is exactly the pressure that produces an ambient global instead.

**The ambient part is a `ContextVar`, never a `threading.local`.** A `ContextVar` is copied
into each `asyncio.Task` when it is created and is private to that task thereafter, so
requests sharing one event-loop thread cannot see each other's. A thread-local is shared by
every coroutine on the thread, which under any concurrency at all hands one request's caller
to another — a leak whose visibility depends on scheduling, so it survives tests and fails in
production.
"""

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from contextvars import ContextVar
from http import HTTPStatus
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .errors import NoRequestContextError, ResponseCommittedError


class _MultiValued(Mapping[str, str]):
    """A mapping where one name may carry several values.

    `__getitem__` returns the first, because that is what a caller asking for "the" value
    means; `get_all` returns every one. Both HTTP headers and a query string work this way,
    and collapsing them to a plain `dict` silently discards the repeats.
    """

    __slots__ = ("_values",)

    def __init__(self, items: Iterable[tuple[str, str]] = (), /) -> None:
        """Record every (name, value) pair, preserving order and repeats."""
        values: dict[str, list[str]] = {}
        for name, value in items:
            values.setdefault(self._key(name), []).append(value)
        self._values = values

    @staticmethod
    def _key(name: str) -> str:
        """Normalise a name. Overridden where lookup is case-insensitive."""
        return name

    def __getitem__(self, name: str) -> str:
        """The first value recorded for `name`."""
        return self._values[self._key(name)][0]

    def __iter__(self) -> Iterator[str]:
        """Every name, in the order first seen."""
        return iter(self._values)

    def __len__(self) -> int:
        """How many distinct names there are."""
        return len(self._values)

    def get_all(self, name: str, /) -> tuple[str, ...]:
        """Every value recorded for `name`, in order. Empty when there is none."""
        return tuple(self._values.get(self._key(name), ()))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._values!r})"


class Headers(_MultiValued):
    """The headers a request arrived with. Lookup is case-insensitive.

    HTTP header names are case-insensitive by specification, so `context.headers["X-Tenant"]`
    and `context.headers["x-tenant"]` must be the same lookup. Normalising on the way in costs
    one `lower()` per header and removes a whole class of bug that only shows up against a
    client that capitalises differently.
    """

    __slots__ = ()

    @staticmethod
    def _key(name: str) -> str:
        """Lower-case, so any capitalisation finds the same value."""
        return name.lower()


class QueryValues(_MultiValued):
    """The query string a request arrived with. Lookup is case-sensitive.

    Unlike header names, query parameter names are opaque and case matters — `?Tag=` and
    `?tag=` are different parameters.
    """

    __slots__ = ()


class Cookie(BaseModel):
    """A cookie to set on the response.

    A model rather than a pile of keyword arguments on `set_cookie`, so a handler can build
    one, pass it around, and have it validated once — and so the eventual set of attributes is
    named in one place instead of duplicated at every call site.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str
    max_age: int | None = None
    path: str = "/"
    domain: str | None = None
    secure: bool = False
    http_only: bool = False
    same_site: Literal["lax", "strict", "none"] = "lax"


class RequestContext:
    """Everything about one invocation that is not the request model itself.

    A slotted class rather than a pydantic model: dexter builds one per request out of data
    the transport has already parsed, and no consumer constructs one from untrusted input — so
    paying roughly eight times the construction cost to validate it would buy nothing. The
    same call `dexter.cqrs` makes for its envelope.

    The inbound half is settled at construction and never changes. The outbound half —
    `set_status`, `set_header`, `set_cookie` — is a small mutable buffer the transport drains
    once the handler has returned.

    `method`, `path`, `url` and `scheme` describe how the caller reached this invocation. They
    are plain strings rather than an HTTP enum so a protocol that has no verb can fill in what
    it does have, or leave them empty, without this type growing a transport in it.
    """

    __slots__ = (
        "_committed",
        "_cookies",
        "_headers",
        "_status",
        "client_host",
        "client_port",
        "cookies",
        "handler",
        "headers",
        "method",
        "path",
        "path_params",
        "query",
        "scheme",
        "state",
        "url",
    )

    def __init__(  # noqa: PLR0913 - a record; every one of these is a distinct fact
        self,
        *,
        handler: type[Any],
        method: str = "",
        path: str = "",
        url: str = "",
        scheme: str = "",
        headers: Headers | None = None,
        cookies: Mapping[str, str] | None = None,
        query: QueryValues | None = None,
        path_params: Mapping[str, str] | None = None,
        client_host: str | None = None,
        client_port: int | None = None,
    ) -> None:
        """Record the operation being invoked and the metadata that came with it."""
        self.handler = handler
        self.method = method
        self.path = path
        self.url = url
        self.scheme = scheme
        self.headers = headers if headers is not None else Headers()
        self.cookies: Mapping[str, str] = {} if cookies is None else dict(cookies)
        self.query = query if query is not None else QueryValues()
        self.path_params: Mapping[str, str] = (
            {} if path_params is None else dict(path_params)
        )
        self.client_host = client_host
        self.client_port = client_port

        self.state: dict[str, Any] = {}
        """Middleware's scratch space, read by whatever comes after it.

        This is the sanctioned replacement for smuggling per-request values through a global.
        Prefer a typed binding where you can — a `Scope.SCOPED` factory reading this context
        gives the reader a real type instead of a string key — and use this when the value is
        genuinely incidental.
        """

        self._status: HTTPStatus | None = None
        self._headers: list[tuple[str, str]] = []
        self._cookies: list[Cookie] = []
        self._committed = False

    # ── the response ─────────────────────────────────────────────────

    @property
    def status(self) -> HTTPStatus | None:
        """The status a handler asked for, or `None` to use the exposure's own."""
        return self._status

    def set_status(self, status: HTTPStatus, /) -> None:
        """Override the status this response is sent with."""
        self._ensure_open()
        self._status = status

    def set_header(self, name: str, value: str, /) -> None:
        """Add a header to the response. Repeating a name adds another value."""
        self._ensure_open()
        self._headers.append((name, value))

    def set_cookie(self, cookie: Cookie, /) -> None:
        """Set a cookie on the response."""
        self._ensure_open()
        self._cookies.append(cookie)

    def response_headers(self) -> tuple[tuple[str, str], ...]:
        """Every header a handler or middleware added, in order."""
        return tuple(self._headers)

    def response_cookies(self) -> tuple[Cookie, ...]:
        """Every cookie a handler or middleware set, in order."""
        return tuple(self._cookies)

    def commit(self) -> None:
        """Close the response to further changes. Idempotent."""
        self._committed = True

    def _ensure_open(self) -> None:
        """Refuse a change once the response has been built."""
        if self._committed:
            raise ResponseCommittedError(
                "the response has already been built, so it can no longer be changed. "
                "Something is holding this context beyond the request it describes."
            )

    def __repr__(self) -> str:
        return f"RequestContext(handler={self.handler.__name__}, path={self.path!r})"


# ── the request bound to this task ───────────────────────────────────

_CURRENT: ContextVar[RequestContext] = ContextVar("dexter.api.request")


def current_request() -> RequestContext:
    """The request being served on this task.

    This is also the provider `use_api` binds `RequestContext` to, which is why it is a plain
    function rather than a method on anything.

    Raises:
        NoRequestContextError: If no request is bound to this task.
    """
    try:
        return _CURRENT.get()
    except LookupError as error:
        raise NoRequestContextError(
            "no request is bound to this task, so there is no RequestContext to resolve. "
            "It exists only while a request is being served; in a test, wrap the call in "
            "`bind_request(context)`."
        ) from error


@contextlib.contextmanager
def bind_request(context: RequestContext) -> Iterator[RequestContext]:
    """Make `context` the request for the duration of the block, on this task only.

    The transport calls this around one request. A test calls it to exercise a handler that
    resolves `RequestContext` without standing up a server::

        with bind_request(make_context()):
            ...

    Leaving the block commits the context — so anything still holding it and trying to set a
    header gets `ResponseCommittedError` rather than silently doing nothing — and restores
    whatever was bound before.
    """
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        context.commit()
        _CURRENT.reset(token)
