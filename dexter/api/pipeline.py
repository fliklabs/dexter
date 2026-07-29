"""The middleware pipeline: what every request passes through on its way to a handler.

A pipeline holds middleware *classes* in registration order, for the same reason a registry
holds handler classes — the container constructs them per request, so each follows the
lifetime it was registered with rather than the pipeline's.

Order is the order they were registered, outermost first. The first middleware registered sees
the request before every other one and sees the response after every other one, which is what
makes "register authentication first" mean what a reader expects.
"""

from typing import Any

from dexter.commons import describe_type
from dexter.dependency_injection import Container

from ._introspection import check_shape
from .errors import DuplicateApiMiddlewareError
from .models import ApiNext, Invocation


class ApiPipeline:
    """The ordered middleware every request runs through.

    One pipeline for every exposure of every handler, so a concern is registered once and
    applies however a handler is reached. Middleware that only cares about one protocol can
    look at `invocation.exposure`.
    """

    __slots__ = ("_middleware",)

    def __init__(self) -> None:
        """Start with an empty pipeline, which runs handlers directly."""
        self._middleware: list[type[Any]] = []

    def add(self, middleware: type[Any], /) -> None:
        """Append `middleware` to the pipeline, inside everything already registered."""
        check_shape(middleware, "middleware")
        if middleware in self._middleware:
            raise DuplicateApiMiddlewareError(
                f"{describe_type(middleware)} is already in the pipeline; "
                f"it would run twice."
            )
        self._middleware.append(middleware)

    def registrations(self) -> tuple[type[Any], ...]:
        """Every middleware class, outermost first."""
        return tuple(self._middleware)

    async def run(
        self, container: Container, invocation: Invocation, terminal: ApiNext
    ) -> Any:
        """Run `terminal` wrapped in every middleware, and return whatever comes back.

        Middleware is resolved from `container` — the scope opened for this request — so a
        scoped middleware sees the same `RequestContext` and the same per-request state its
        handler does. An empty pipeline calls `terminal` directly rather than building a chain
        of one.
        """
        if not self._middleware:
            return await terminal(invocation)

        instances = [await container.resolve(key) for key in self._middleware]

        def link(index: int) -> ApiNext:
            if index == len(instances):
                return terminal
            instance = instances[index]

            async def call(next_invocation: Invocation) -> Any:
                return await instance.handle(next_invocation, link(index + 1))

            return call

        return await link(0)(invocation)
