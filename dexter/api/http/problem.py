"""Rendering every failure as problem details, whichever layer raised it.

Without this, a service built on `dexter.api` answers failures in four shapes: what this module
renders for a mapped domain exception, the framework's own shape for an `HTTPException`, a
third for a validation failure, and `text/plain` for anything unhandled. Each is defensible on
its own and the set is not — a caller needs four parsers, and which one they need depends on
which layer happened to fail.

So `install` replaces the framework's own handlers with ones that render the same body. One
media type, one set of fields, whatever went wrong.

**The `Exception` handler is the subtle one, and the subtlety is load-bearing.** Registering
under that key does not put a handler in the ordinary table: the routing layer pulls `500` and
`Exception` out and gives them to the outermost error middleware, which sends the response and
then **re-raises**. That re-raise is what a server, and every error tracker attached to one,
logs from. Rendering the 500 there rather than catching it earlier is what buys a uniform body
without silencing the bug that produced it — returning a response and raising are different
things, and only one of them leaves a trace.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..models import ErrorResponse, InvalidField

PROBLEM_JSON = "application/problem+json"
"""Media type for a failure, per RFC 9457."""

UNEXPECTED = "Internal Server Error"
"""What an unmapped failure tells the caller.

Deliberately says nothing. Registering an exception with `register_error` is the author's
statement that its message is safe to show; nothing else has been through that judgement, and
`str()` of an arbitrary exception carries connection strings, file paths and query fragments.
The real message is in the logs, where it belongs.
"""


def problem(
    status: int,
    title: str | None,
    detail: str,
    *,
    errors: tuple[InvalidField, ...] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render one failure as problem details.

    The single place the media type is decided, so no path can drift out of step with the
    others. `status` is whatever the caller chose, including a code the standard does not
    name — rounding it to a recognised one would answer a question nobody asked.
    """
    body = ErrorResponse.of(status, title, detail, errors)
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


def install(app: FastAPI) -> None:
    """Render the framework's own failures as problem details too.

    Replaces only the framework's defaults. It has already installed handlers for the two
    exceptions it owns by the time an application reaches here, so "register if absent" would
    never fire for them — they are recognised by identity instead, and a consumer who installed
    their own keeps it. Nothing handles `Exception` by default, so for that one absence is the
    test.

    The key is **starlette's** `HTTPException`, not the one `fastapi` re-exports. They are
    different classes — the latter subclasses the former — and the default handler is
    registered under the base. Keying on the subclass looks right, matches nothing, and
    silently leaves the framework's own handler in place.
    """
    handlers: dict[Any, Any] = app.exception_handlers

    if handlers.get(HTTPException) is http_exception_handler:
        app.add_exception_handler(HTTPException, _http_exception)
    if handlers.get(RequestValidationError) is request_validation_exception_handler:
        app.add_exception_handler(RequestValidationError, _validation)
    if Exception not in handlers:
        app.add_exception_handler(Exception, _unexpected)


async def _http_exception(request: Request, exc: Exception) -> Response:
    """Render an `HTTPException` the caller's own way.

    Its status and message are kept: a handler raising one has said what it wants reported.
    `headers` is carried across because a 401 without its `WWW-Authenticate` is a broken 401,
    and the framework's own handler carries it for the same reason.
    """
    del request
    failure = exc if isinstance(exc, HTTPException) else HTTPException(500)
    return problem(
        failure.status_code,
        None,
        str(failure.detail),
        headers=dict(failure.headers) if failure.headers else None,
    )


async def _validation(request: Request, exc: Exception) -> Response:
    """Render a validation failure, keeping which fields were rejected and why."""
    del request
    reported = exc.errors() if isinstance(exc, RequestValidationError) else []
    fields = tuple(
        InvalidField(
            location=tuple(entry.get("loc", ())),
            message=str(entry.get("msg", "")),
            kind=str(entry.get("type", "")),
        )
        for entry in reported
    )
    return problem(
        int(HTTPStatus.UNPROCESSABLE_CONTENT),
        None,
        "The request could not be validated.",
        errors=fields,
    )


async def _unexpected(request: Request, exc: Exception) -> Response:
    """Render an unhandled failure, saying nothing about it.

    `exc` is never read. Anything reaching here is a bug nobody anticipated, so there is no
    judgement on record about whether its message can be shown — and the middleware that calls
    this re-raises afterwards, so the message is not lost, only withheld.
    """
    del request, exc
    return problem(int(HTTPStatus.INTERNAL_SERVER_ERROR), None, UNEXPECTED)
