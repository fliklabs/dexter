"""Building the application from what was registered.

`create_app` reads the exposure registry and mounts a route for every `HttpExposure` in it.
It is the HTTP half of the seam: a second protocol would read the same registry, ask for its
own kind of exposure, and build something else entirely from the same handlers.

**It starts nothing.** No server, no event loop, no background task — it returns an ASGI
application and whoever serves it decides how. That is the same division `dexter.cli` draws:
the library hands back something runnable, and the three lines that start a loop belong to the
consumer.
"""

import inspect

from fastapi import FastAPI

from dexter.commons import describe_type
from dexter.dependency_injection import Container

from ..errors import ApiNotWiredError
from ..exposure import HttpExposure
from ..registry import ExposureRegistry
from .endpoint import build_endpoint
from .problem import install


async def create_app(
    container: Container, *, app: FastAPI | None = None, prefix: str = ""
) -> FastAPI:
    """Add a route for every registered HTTP exposure, and return the application.

    Args:
        container: A container `use_api` was wired into. Each request opens a scope of *this*
            container, so it must be the root rather than a scope of one.
        app: An application to mount onto. Pass your own to set a title, a version, a
            lifespan, documentation URLs, or any framework middleware — mirroring that whole
            keyword surface here would only be a second, worse copy of it.
        prefix: Prepended to every path, for example `"/api/v1"`.

    Returns:
        The application, with one route per exposure. Nothing has been started.

    Raises:
        ApiNotWiredError: If `use_api` was never called on the builder this container came
            from.

    A coroutine, because every read from a built container is one. The consequence is worth
    stating plainly: an application object cannot be built at import time, so a server that
    takes a module path to one is not how this is served. The entry point is instead::

        async def main() -> None:
            container = build_container()
            app = await create_app(container)
            try:
                await serve(app)
            finally:
                await container.aclose()

    **Routes are added in registration order**, and matching follows it — so a literal path
    has to be registered before a pattern that would also match it. Reordering them here would
    be a rule that silently does the right thing until the day it does the wrong one.

    The container is not closed by the application. dexter did not create it and does not own
    it; the `try`/`finally` above says so where a reader can see it.
    """
    registry = await _registry(container)
    target = FastAPI() if app is None else app

    # So that a failure the framework reports answers in the same shape a mapped one does.
    # Replaces only the framework's own defaults; see `problem.install`.
    install(target)

    for record, exposure in registry.of(HttpExposure):
        target.add_api_route(
            path=f"{prefix}{exposure.path}",
            endpoint=build_endpoint(container, record, exposure),
            methods=[str(exposure.method)],
            status_code=int(exposure.status),
            response_model=record.response_model,
            tags=list(exposure.tags),
            summary=exposure.summary,
            description=exposure.description or inspect.getdoc(record.handler) or "",
            name=exposure.name,
            deprecated=exposure.deprecated or None,
            include_in_schema=exposure.include_in_schema,
        )

    return target


async def _registry(container: Container) -> ExposureRegistry:
    """The exposure registry, or an explanation that wiring is missing."""
    registry = await container.try_resolve(ExposureRegistry)
    if registry is None:
        raise ApiNotWiredError(
            f"{describe_type(ExposureRegistry)} is not registered, so there is nothing to "
            f"serve. Call `use_api(builder)` before building the container."
        )
    return registry
