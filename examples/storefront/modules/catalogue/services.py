"""The catalogue's implementation.

A dictionary standing in for a database. Everything a real one would need — a connection, a
transaction, a retry — arrives the same way: as an annotated constructor parameter.
"""

import asyncio

from .domain import Catalogue, Product, UnknownProductError

STOCK = (
    Product(sku="DX-100", name="Widget", pence=1250, in_stock=8),
    Product(sku="DX-200", name="Sprocket", pence=450, in_stock=3),
    Product(sku="DX-300", name="Flange", pence=9900, in_stock=0),
)
"""What the shop opens with. A real one would read this from somewhere."""


class InMemoryCatalogue(Catalogue):
    """A `Catalogue` holding products in a dictionary.

    Bound `Scope.SINGLETON`: it stands in for a database, and a database does not restart per
    request.
    """

    def __init__(self) -> None:
        """Start with the opening stock."""
        self._products = {product.sku: product for product in STOCK}

    async def get(self, sku: str) -> Product:
        """Return one product, or say which one was asked for."""
        await asyncio.sleep(0)
        product = self._products.get(sku)
        if product is None:
            raise UnknownProductError(f"no product {sku}")
        return product

    async def list(self, *, in_stock_only: bool) -> list[Product]:
        """Return every product, optionally only those with stock."""
        await asyncio.sleep(0)
        products = list(self._products.values())
        if in_stock_only:
            products = [product for product in products if product.in_stock]
        return products

    async def take(self, sku: str, quantity: int) -> None:
        """Remove stock. Reserving more than exists is the caller's problem to check."""
        product = await self.get(sku)
        remaining = max(0, product.in_stock - quantity)
        self._products[sku] = product.model_copy(update={"in_stock": remaining})
