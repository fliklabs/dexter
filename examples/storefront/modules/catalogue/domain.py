"""What the catalogue is, independent of how any of it works.

Everything here is either a message the module answers or a contract it depends on. Nothing
mentions dexter, HTTP or a database: a domain that knows how it is delivered is a domain that
cannot be delivered a second way.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field

from dexter.cqrs import Query


class Product(BaseModel):
    """One thing that can be bought."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    name: str
    pence: int = Field(description="Price in pence, because money is not a float.")
    in_stock: int


class UnknownProductError(Exception):
    """No product has that SKU."""


# ── what the module answers ──────────────────────────────────────────


class GetProduct(Query[Product]):
    """Read one product."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str


class ListProducts(Query[list[Product]]):
    """List what is for sale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    in_stock_only: bool = False


# ── what the module depends on ───────────────────────────────────────


class Catalogue(ABC):
    """Where products live. An abstract class, used directly as a container key.

    Other modules depend on *this*, never on the class that implements it — which is what lets
    orders price a line without importing anything from the catalogue's insides.
    """

    @abstractmethod
    async def get(self, sku: str) -> Product:
        """Return one product, or raise `UnknownProductError`."""

    @abstractmethod
    async def list(self, *, in_stock_only: bool) -> list[Product]:
        """Return every product, optionally only those with stock."""

    @abstractmethod
    async def take(self, sku: str, quantity: int) -> None:
        """Remove stock, or raise `UnknownProductError`."""
