"""How a handler can be reached.

An `Exposure` is a *declaration*, not machinery: it names a way in and says nothing about how
that way is served. That is the whole reason it lives here rather than under `http/` — a route
table is readable, testable and inspectable without a web framework being involved at all.

**This is the module's protocol seam, and it is deliberately thin.** One handler carries any
number of exposures; a transport asks the registry for the kind it understands and ignores the
rest. Adding GraphQL later is a `GraphqlExposure` subclass, a builder that asks for it, and
nothing else — no change to the handler contract, the registry, the pipeline or the wiring.
What is *not* here is as considered as what is: no transport enum, no adapter base class, no
per-protocol registry. Each would be structure with no behaviour behind it until a second
protocol exists to give it some.
"""

import re
from enum import StrEnum
from http import HTTPMethod, HTTPStatus

from pydantic import BaseModel, ConfigDict, field_validator

from .errors import InvalidExposureError

_PLACEHOLDER = re.compile(r"\{([^{}:]+)(?::[^{}]+)?\}")
"""Matches `{name}` and `{name:converter}`, capturing the name.

A regex rather than the routing library's own parser, because this module must not import one.
The converter suffix is tolerated so a path a transport supports does not fail here.
"""

_BODYLESS = frozenset(
    {
        HTTPMethod.GET,
        HTTPMethod.HEAD,
        HTTPMethod.DELETE,
        HTTPMethod.OPTIONS,
        HTTPMethod.TRACE,
    }
)
"""Methods whose payload is conventionally the query string rather than a body."""


class PayloadSource(StrEnum):
    """Where the fields the path does not name are read from."""

    BODY = "BODY"
    """From the request body, as one JSON object."""

    QUERY = "QUERY"
    """From the query string, one parameter per field."""


def describe_source(source: PayloadSource) -> str:
    """Render a payload source as the symbol a caller would type.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence. In a message aimed
    at a developer the qualified symbol is more useful: it is what they have to write.
    """
    return f"PayloadSource.{source.name}"


def path_parameters(path: str, /) -> tuple[str, ...]:
    """Every `{name}` in `path`, in order.

    Names repeat at most once each; a duplicate is rejected when the exposure is built.
    """
    return tuple(match.group(1) for match in _PLACEHOLDER.finditer(path))


def default_payload(method: HTTPMethod, /) -> PayloadSource:
    """Where a method's payload comes from when the exposure does not say."""
    return PayloadSource.QUERY if method in _BODYLESS else PayloadSource.BODY


class Exposure(BaseModel):
    """Base for every way a handler can be reached.

    Frozen and `extra="forbid"`, like every dexter type built once from what a consumer wrote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class HttpExposure(Exposure):
    """One handler, reachable at one method and path.

    `tags` is a tuple rather than a list on purpose: a frozen pydantic model is only shallowly
    frozen, and a list field would silently make the whole model unhashable.
    """

    method: HTTPMethod
    path: str
    status: HTTPStatus = HTTPStatus.OK
    payload: PayloadSource | None = None
    """Where non-path fields are read from. `None` derives it from the method."""

    tags: tuple[str, ...] = ()
    summary: str | None = None
    description: str | None = None
    """Defaults to the handler's own docstring, so one text documents both."""

    name: str | None = None
    deprecated: bool = False
    include_in_schema: bool = True

    @field_validator("path")
    @classmethod
    def _check_path(cls, path: str) -> str:
        """Reject a path that cannot describe a route.

        Checked here rather than at registration so the traceback points at the consumer's own
        wiring line, which is where the mistake is.
        """
        if not path.startswith("/"):
            raise InvalidExposureError(
                f"path {path!r} must start with '/'; paths are absolute."
            )
        if path.count("{") != path.count("}"):
            raise InvalidExposureError(f"path {path!r} has an unbalanced '{{' or '}}'.")

        names = path_parameters(path)
        duplicated = {name for name in names if names.count(name) > 1}
        if duplicated:
            listed = ", ".join(sorted(duplicated))
            raise InvalidExposureError(
                f"path {path!r} names {listed} more than once; one field cannot be read "
                f"from two places."
            )
        return path

    @property
    def source(self) -> PayloadSource:
        """Where non-path fields are read from, with the method's default applied."""
        return (
            self.payload if self.payload is not None else default_payload(self.method)
        )

    @property
    def parameters(self) -> tuple[str, ...]:
        """Every field name the path reads."""
        return path_parameters(self.path)
