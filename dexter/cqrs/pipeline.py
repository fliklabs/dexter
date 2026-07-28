"""The middleware pipeline: what every dispatch passes through on its way to a handler.

A pipeline holds middleware *classes* in registration order, for the same reason a registry
holds handler classes — the container constructs them at dispatch time, so each follows the
lifetime it was registered with rather than the pipeline's.

Order is the order they were registered, outermost first. The first middleware registered sees
the dispatch before every other one and sees the result after every other one, which is what
makes "register logging first" mean what a reader expects.
"""

from typing import Any

from dexter.commons import describe_type
from dexter.dependency_injection import Container

from ._introspection import check_shape
from .errors import DuplicateMiddlewareError
from .models import Envelope, Next


class MiddlewarePipeline:
    """The ordered middleware every bus runs a dispatch through.

    One pipeline is shared by all three buses, so a concern is registered once and applies to
    commands, queries and events alike. Middleware that only cares about one of them can look
    at `envelope.message`.
    """

    __slots__ = ("_middleware",)

    def __init__(self) -> None:
        """Start with an empty pipeline, which runs handlers directly."""
        self._middleware: list[type[Any]] = []

    def add(self, middleware: type[Any], /) -> None:
        """Append `middleware` to the pipeline, inside everything already registered."""
        check_shape(middleware, "middleware")
        if middleware in self._middleware:
            raise DuplicateMiddlewareError(
                f"{describe_type(middleware)} is already in the pipeline; "
                f"it would run twice."
            )
        self._middleware.append(middleware)

    def registrations(self) -> tuple[type[Any], ...]:
        """Every middleware class, outermost first."""
        return tuple(self._middleware)

    async def run(
        self, container: Container, envelope: Envelope[Any], terminal: Next
    ) -> Any:
        """Run `terminal` wrapped in every middleware, and return whatever comes back.

        Middleware is resolved from `container` — the scope the bus belongs to — so a scoped
        middleware sees the same per-request state its handlers do. An empty pipeline calls
        `terminal` directly rather than building a chain of one.
        """
        if not self._middleware:
            return await terminal(envelope)

        instances = [await container.resolve(key) for key in self._middleware]

        def link(index: int) -> Next:
            if index == len(instances):
                return terminal
            instance = instances[index]

            async def call(next_envelope: Envelope[Any]) -> Any:
                return await instance.handle(next_envelope, link(index + 1))

            return call

        return await link(0)(envelope)
