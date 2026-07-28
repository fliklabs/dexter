"""Cross-cutting concerns, written once and applied to all three buses.

Middleware is where a concern that has nothing to do with any particular message lives.
Registration order is the nesting order, outermost first — so `Tracing` below wraps
`Correlate`, and both wrap every handler.
"""

from typing import Any

from dexter.cqrs import Envelope, Next

from .display import line, short
from .services import DispatchContext


class Tracing:
    """Prints one line as a dispatch goes down, and another as it comes back.

    Deliberately not indented by depth. Several dispatches are in flight at once here, so a
    shared depth counter would draw them as nested when they are merely concurrent — the id on
    each line is what actually pairs the two halves.
    """

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        """Announce the dispatch, run the rest of the pipeline, then announce the result."""
        name = type(envelope.message).__name__
        line(f"-> {name:12} {short(envelope.id)}")
        result = await call_next(envelope)
        line(f"<- {name:12} {short(envelope.id)}")
        return result


class Correlate:
    """Records the envelope being handled, so handlers can chain what they publish.

    A handler is given the message, not the envelope. Putting the envelope in a scoped
    `DispatchContext` is what lets `PlaceOrderHandler` mark `OrderPlaced` as caused by the
    `PlaceOrder` it is handling.
    """

    def __init__(self, context: DispatchContext) -> None:
        """Take this scope's dispatch context."""
        self.context = context

    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        """Publish the envelope into the context for the duration of the dispatch."""
        previous = self.context.current
        self.context.current = envelope
        try:
            return await call_next(envelope)
        finally:
            self.context.current = previous
