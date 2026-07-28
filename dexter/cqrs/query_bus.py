"""The query bus: ask a question, get the answer.

Unlike the other two, a query is answered inline — `await query_bus.ask(query)` returns the
result directly rather than a ticket. A read has no side effect worth deferring and no
identity worth correlating, so deferring one would only make it a slower read while forcing
every call site to redeem a ticket it never wanted.

The envelope is still built, because middleware sees every dispatch the same way and a query
belongs in the same trace as the command that triggered it.
"""

from abc import ABC, abstractmethod
from typing import Any, cast

from dexter.dependency_injection import Container

from .bus import MessageBus
from .models import Envelope, Query
from .pipeline import MiddlewarePipeline
from .registry import QueryRegistry


class QueryBus(MessageBus, ABC):
    """Answers queries from the single handler registered for each."""

    __slots__ = ()

    @property
    def name(self) -> str:
        """What this bus carries."""
        return "query"

    @abstractmethod
    async def ask[TResult](
        self,
        query: Query[TResult],
        /,
        *,
        caused_by: Envelope[Any] | None = None,
    ) -> TResult:
        """Answer `query`, raising `UnhandledQueryError` if nothing handles it."""


class InProcessQueryBus(QueryBus):
    """Answers each query on the calling task, with no ticket and no background work.

    Registered `Scope.SCOPED` for the same reason as the other buses: it resolves handlers
    from the container it was given, so its lifetime decides which container those handlers
    come from.
    """

    __slots__ = ("_container", "_pipeline", "_registry")

    def __init__(
        self,
        container: Container,
        registry: QueryRegistry,
        pipeline: MiddlewarePipeline,
    ) -> None:
        """Take the resolving container, the query registry, and the shared pipeline."""
        super().__init__()
        self._container = container
        self._registry = registry
        self._pipeline = pipeline

    async def ask[TResult](
        self,
        query: Query[TResult],
        /,
        *,
        caused_by: Envelope[Any] | None = None,
    ) -> TResult:
        """Answer `query` inline and return its result."""
        self._ensure_open()
        handler_key = self._registry.resolve(type(query))
        envelope = Envelope.wrap(query, caused_by=caused_by)

        async def run(target: Envelope[Any]) -> Any:
            handler = await self._container.resolve(handler_key)
            return await handler.handle(target.message)

        # Middleware is generic over every message, so the pipeline's result is `Any`. The
        # registration-time check in `_introspection` is what makes this cast honest: a
        # handler whose return type disagrees with its query never gets registered.
        return cast("TResult", await self._pipeline.run(self._container, envelope, run))
