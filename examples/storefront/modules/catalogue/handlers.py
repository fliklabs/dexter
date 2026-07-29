"""The catalogue's handlers: two queries and the API that reaches them.

Two kinds of handler live here, and the difference is worth seeing side by side. A **query
handler** answers a message and knows nothing about how it arrived. An **API handler** is the
edge: it turns a request into that message and the answer into a response.

The API handlers are deliberately thin. What a product *is* stays in the query handler, where
a worker, a scheduled job or another module can reach it through the bus — the HTTP route is
one way in, not the only one.
"""

from pydantic import BaseModel, ConfigDict, Field

from dexter.cqrs import QueryBus

from .domain import Catalogue, GetProduct, ListProducts, Product

# ── the application core ─────────────────────────────────────────────


class GetProductHandler:
    """Answers one product."""

    def __init__(self, catalogue: Catalogue) -> None:
        """Take the catalogue. The container supplies whichever one is bound."""
        self.catalogue = catalogue

    async def handle(self, query: GetProduct) -> Product:
        """Read the product, or fail if there is none."""
        return await self.catalogue.get(query.sku)


class ListProductsHandler:
    """Answers what is for sale."""

    def __init__(self, catalogue: Catalogue) -> None:
        """Take the catalogue."""
        self.catalogue = catalogue

    async def handle(self, query: ListProducts) -> list[Product]:
        """List the products."""
        return await self.catalogue.list(in_stock_only=query.in_stock_only)


# ── the API edge ─────────────────────────────────────────────────────


class GetProductRequest(BaseModel):
    """Read one product. `sku` is read from the path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(description="The product's code, such as DX-100.")


class ListProductsRequest(BaseModel):
    """List products. Every field is read from the query string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    in_stock_only: bool = Field(default=False, description="Hide anything sold out.")


class GetProductApi:
    """Read one product."""

    def __init__(self, queries: QueryBus) -> None:
        """Take the query bus."""
        self.queries = queries

    async def handle(self, request: GetProductRequest) -> Product:
        """Ask for the product and hand it straight back."""
        return await self.queries.ask(GetProduct(sku=request.sku))


class ListProductsApi:
    """List what is for sale."""

    def __init__(self, queries: QueryBus) -> None:
        """Take the query bus."""
        self.queries = queries

    async def handle(self, request: ListProductsRequest) -> list[Product]:
        """Ask for the list and hand it straight back."""
        return await self.queries.ask(ListProducts(in_stock_only=request.in_stock_only))
