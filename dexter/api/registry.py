"""What is exposed, and what a failure means.

Two registries, both populated while wiring and read-only in practice thereafter: `use_api`
binds each as an instance, the `register_*` functions fill them, and the container is built
afterwards.

`ExposureRegistry` holds *classes*, never instances — the container constructs a handler per
request, so a handler's own dependencies follow the lifetime it was registered with rather
than the registry's. It is also where a handler's request and response models are recorded,
because reading them costs introspection and nothing about them changes after registration.
"""

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel

from dexter.commons import describe_type

from ._introspection import read_contract
from .errors import (
    DuplicateExposureError,
    DuplicateRouteError,
    InvalidErrorMappingError,
    InvalidExposureError,
)
from .exposure import Exposure, HttpExposure


class ExposureRecord:
    """One handler, its contract, and every way it can be reached."""

    __slots__ = ("exposures", "handler", "request_model", "response_model")

    def __init__(
        self,
        handler: type[Any],
        request_model: type[BaseModel],
        response_model: Any,
        exposures: tuple[Exposure, ...],
    ) -> None:
        """Record the handler, what it accepts and serves, and its exposures."""
        self.handler = handler
        self.request_model = request_model
        self.response_model = response_model
        self.exposures = exposures

    def __repr__(self) -> str:
        return f"ExposureRecord({describe_type(self.handler)})"


class ExposureRegistry:
    """Every registered handler, queryable by the kind of exposure a transport understands."""

    __slots__ = ("_records", "_routes")

    def __init__(self) -> None:
        """Start with nothing registered."""
        self._records: dict[type[Any], ExposureRecord] = {}
        self._routes: dict[tuple[str, str], type[Any]] = {}

    def register(
        self, handler: type[Any], exposures: tuple[Exposure, ...], /
    ) -> ExposureRecord:
        """Record `handler` and every way it can be reached.

        Raises `DuplicateExposureError` if the handler is already registered: it would be
        bound twice in the container and its `scope=` would then be ambiguous. Pass every
        exposure to one call.
        """
        if handler in self._records:
            raise DuplicateExposureError(
                f"{describe_type(handler)} is already registered. Pass every exposure to a "
                f"single `register_handler` call, or write a handler per operation."
            )
        if not exposures:
            raise InvalidExposureError(
                f"{describe_type(handler)} was registered with no exposures, so nothing "
                f"could ever reach it."
            )

        request_model, response_model = read_contract(handler)
        for exposure in exposures:
            self._check(request_model, exposure)

        record = ExposureRecord(handler, request_model, response_model, exposures)
        self._records[handler] = record
        for exposure in exposures:
            if isinstance(exposure, HttpExposure):
                self._routes[str(exposure.method), exposure.path] = handler
        return record

    def of[E: Exposure](self, kind: type[E], /) -> tuple[tuple[ExposureRecord, E], ...]:
        """Every (record, exposure) pair whose exposure is a `kind`, in registration order.

        This is the whole protocol seam. A transport asks for the exposure it understands and
        never sees the others, so a handler exposed three ways is still one handler.
        """
        return tuple(
            (record, exposure)
            for record in self._records.values()
            for exposure in record.exposures
            if isinstance(exposure, kind)
        )

    def records(self) -> tuple[ExposureRecord, ...]:
        """Every registered handler, in registration order."""
        return tuple(self._records.values())

    def is_registered(self, handler: type[Any], /) -> bool:
        """Whether `handler` has been registered."""
        return handler in self._records

    def _check(self, request_model: type[BaseModel], exposure: Exposure, /) -> None:
        """Reject an exposure that cannot be served by this handler."""
        if not isinstance(exposure, HttpExposure):
            return

        unknown = [
            name
            for name in exposure.parameters
            if name not in request_model.model_fields
        ]
        if unknown:
            listed = ", ".join(sorted(unknown))
            raise InvalidExposureError(
                f"path {exposure.path!r} names {listed}, which "
                f"{describe_type(request_model)} does not declare. A path parameter is read "
                f"into a field of the request model, so it has to be one."
            )

        route = (str(exposure.method), exposure.path)
        existing = self._routes.get(route)
        if existing is not None:
            raise DuplicateRouteError(
                f"{exposure.method} {exposure.path} is already served by "
                f"{describe_type(existing)}; two handlers cannot claim one route."
            )


class ErrorMapping:
    """One exception class, and what it means to a caller."""

    __slots__ = ("error", "status", "title")

    def __init__(
        self, error: type[Exception], status: HTTPStatus, title: str | None
    ) -> None:
        """Record the exception, the status it reports, and how to name it."""
        self.error = error
        self.status = status
        self.title = title

    def __repr__(self) -> str:
        return f"ErrorMapping({describe_type(self.error)} -> {int(self.status)})"


class ErrorMap:
    """Which domain exceptions mean what to a caller.

    **Lookup walks the MRO, most-derived first**, which deliberately differs from the exact-
    class rule `dexter.cqrs` uses for handlers. An exception hierarchy exists in order to be
    caught by base class — that is what `except` does — so registering a base has to cover its
    subclasses, or every leaf would need its own line. The usual objection to inheritance-based
    lookup, that the winner depends on MRO order, does not bite here: MRO order is exactly the
    order `except` already resolves in.

    Starts empty. Nothing is mapped by default, so an exception nobody registered propagates
    and is reported as the unhandled failure it is.
    """

    __slots__ = ("_mappings",)

    def __init__(self) -> None:
        """Start with nothing mapped."""
        self._mappings: dict[type[Exception], ErrorMapping] = {}

    def register(
        self, error: type[Exception], /, *, status: HTTPStatus, title: str | None = None
    ) -> None:
        """Map `error` and its subclasses to `status`."""
        # Widened deliberately: the annotation says what a caller should pass, and this says
        # what happens when they do not. A type checker cannot police a consumer's wiring.
        candidate: Any = error
        if not (isinstance(candidate, type) and issubclass(candidate, Exception)):
            raise InvalidErrorMappingError(
                f"{error!r} cannot be mapped; only an exception class can be."
            )
        if error in self._mappings:
            raise InvalidErrorMappingError(
                f"{describe_type(error)} is already mapped to "
                f"{int(self._mappings[error].status)}; the winner would depend on the order "
                f"unrelated wiring ran in."
            )
        self._mappings[error] = ErrorMapping(error, status, title)

    def find(self, error: Exception, /) -> ErrorMapping | None:
        """The most derived mapping covering `error`, or `None` if nothing covers it."""
        for klass in type(error).__mro__:
            mapping = self._mappings.get(klass)
            if mapping is not None:
                return mapping
        return None

    def mappings(self) -> tuple[ErrorMapping, ...]:
        """Every mapping, in registration order."""
        return tuple(self._mappings.values())
