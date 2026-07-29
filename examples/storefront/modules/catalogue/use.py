"""Everything the catalogue module contributes. This is the file to read first.

One function, and it is the shape every module in this application follows: bind what the
module implements, register its handlers, map its failures, expose its routes.

**Notice what is not here.** No `use_cqrs`, no `use_api` — those belong to the application and
are called once by `use_application`, before any module runs. A module that wired its own would
work until a second module did the same and the builder refused the repeat. There is also no
mention of any other module: the catalogue does not know orders exists, and orders reaches it
through the `Catalogue` contract rather than through an import.
"""

from http import HTTPMethod, HTTPStatus

from dexter.api import HttpExposure, register_error, register_handler
from dexter.cqrs import register_query_handler
from dexter.dependency_injection import ContainerBuilder, Scope

from .domain import Catalogue, GetProduct, ListProducts, UnknownProductError
from .handlers import (
    GetProductApi,
    GetProductHandler,
    ListProductsApi,
    ListProductsHandler,
)
from .services import InMemoryCatalogue

TAG = "catalogue"
"""Groups this module's routes in the generated schema."""


def use_catalogue(builder: ContainerBuilder) -> None:
    """Register the catalogue: what it stores, what it answers, and how it is reached."""
    # Stands in for a database, so one for the whole process.
    builder.register(Catalogue).to(InMemoryCatalogue, scope=Scope.SINGLETON)

    # What a caller is told when a SKU does not exist. No `scope=`: nothing is constructed.
    register_error(
        builder,
        UnknownProductError,
        status=HTTPStatus.NOT_FOUND,
        title="No such product",
    )

    # A handler holds no state between messages, so a fresh one each time.
    register_query_handler(
        builder, GetProduct, GetProductHandler, scope=Scope.TRANSIENT
    )
    register_query_handler(
        builder, ListProducts, ListProductsHandler, scope=Scope.TRANSIENT
    )

    register_handler(
        builder,
        ListProductsApi,
        HttpExposure(method=HTTPMethod.GET, path="/products", tags=(TAG,)),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        GetProductApi,
        HttpExposure(method=HTTPMethod.GET, path="/products/{sku}", tags=(TAG,)),
        scope=Scope.TRANSIENT,
    )
