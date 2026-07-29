"""Exceptions raised by the API module.

Two conventions carry over from the rest of dexter. `args` stays a short one-line message, so
`pytest.raises(match=...)` and log lines remain readable, while any structured detail the
failure carries is appended by `__str__`. And a wiring mistake is reported while wiring:
everything under `ApiRegistrationError` is raised before the container is ever built.

**There is no `ApiGroupError`.** AGENTS.md prescribes one for failures that are genuinely
plural, and this module has none: one request has one outcome. The plural failure that can
happen while serving one — several instances failing to dispose as the request scope closes —
is `DisposalError`, which belongs to `dexter.dependency_injection` and is already a group.
Adding an unused subclass here to look symmetrical with `dexter.cqrs` would be cargo cult.
"""

from dexter.commons import DexterError


class ApiError(DexterError):
    """Base class for every API failure.

    Consumers can catch this to cover both wiring and request-time problems.
    """


# ── registration ─────────────────────────────────────────────────────


class ApiRegistrationError(ApiError):
    """A handler, middleware or error mapping could not be registered. Raised while wiring."""


class ApiNotWiredError(ApiRegistrationError):
    """`use_api` was never called on this builder.

    The registries a handler registers into are created by `use_api`, so it has to run first.
    Raised instead of the container's own "not registered as an instance" message, which
    names an internal type rather than the call the reader is missing.
    """


class DuplicateExposureError(ApiRegistrationError):
    """The handler is already registered.

    One call registers a handler with every way it can be reached, so a second call is always
    a mistake: the class would be bound twice and its `scope=` would be ambiguous. Pass every
    exposure to one `register_handler`, or write a handler per operation.
    """


class DuplicateRouteError(ApiRegistrationError):
    """Two handlers claim the same method and path.

    Whichever won would depend on the order unrelated wiring ran in, so neither does.
    """


class DuplicateApiMiddlewareError(ApiRegistrationError):
    """The middleware is already in the pipeline, and would run twice."""


class InvalidApiHandlerError(ApiRegistrationError):
    """The handler cannot be used.

    Covers a handler that is not a class, is a `Protocol`, has no `handle` method, whose
    `handle` is not asynchronous or does not take exactly one request, or whose request and
    response types cannot be read.
    """


class InvalidExposureError(ApiRegistrationError):
    """The exposure does not describe a reachable operation.

    A path that does not start with `/`, an unbalanced `{`, or a `{name}` that is not a field
    of the handler's request model — the last of which would leave the parameter unfillable
    at runtime, long after the mistake was made.
    """


class InvalidErrorMappingError(ApiRegistrationError):
    """The exception cannot be mapped to a status.

    Covers something that is not an exception class, and a class mapped twice — where the
    winner would depend on wiring order.
    """


# ── request ──────────────────────────────────────────────────────────


class ApiRequestError(ApiError):
    """Something went wrong while serving a request."""


class NoRequestContextError(ApiRequestError):
    """No request is bound to this task.

    `RequestContext` describes one invocation, so it exists only while one is being served.
    Reaching for it from application startup, a background task that outlived its request, or
    a unit test means there is nothing to describe.
    """


# ── state ────────────────────────────────────────────────────────────


class ApiStateError(ApiError):
    """A request context was used after its response was built."""


class ResponseCommittedError(ApiStateError):
    """The response has been built, so it can no longer be changed.

    Raised rather than ignored: a background task that kept hold of a `RequestContext` and
    sets a header on it has done nothing, and a silent no-op is the hardest possible version
    of that bug to find.
    """
