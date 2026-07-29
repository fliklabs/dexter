"""Typed request handlers, exposed over HTTP and — later — over other protocols.

A handler is an ordinary class with one async method. It takes a pydantic model and returns
one; it names no transport, and nothing about it says HTTP::

    class GetRoomHandler:
        def __init__(self, rooms: RoomStore) -> None:
            self.rooms = rooms

        async def handle(self, request: GetRoom) -> RoomView:
            return await self.rooms.get(request.room_id)

Wiring is the same two shapes every dexter module uses — what the module provides, then what
the application contributes::

    builder = ContainerBuilder()
    use_api(builder)
    register_handler(
        builder,
        GetRoomHandler,
        HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}", tags=("rooms",)),
        scope=Scope.TRANSIENT,
    )
    container = builder.build()

    from dexter.api.http import create_app

    app = await create_app(container)

**Headers and cookies reach a handler by injection, not by argument.** `RequestContext` is
bound `Scope.SCOPED`, so a handler — or a repository three levels beneath it — declares an
ordinary constructor parameter and receives the one for the request being served. Nothing is
threaded by hand and nothing is smuggled through a global.

**`create_app` lives in `dexter.api.http`, not here.** Everything in this package is
transport-agnostic and importing it pulls in no web framework; the HTTP adapter is a package
of its own so that the boundary is real rather than a convention. When a second protocol
lands, it becomes `dexter.api.graphql` beside it, serving the same handlers.

**A request is one container scope.** It opens after the request has been validated and
closes *before* the response is produced, so anything registered with `dispose=` — a CQRS bus
settling the commands a handler dispatched, a unit of work committing — has finished before
the caller is told anything. This module never imports `dexter.cqrs` to achieve that; the
container does it.
"""

from .context import Cookie as Cookie
from .context import Headers as Headers
from .context import QueryValues as QueryValues
from .context import RequestContext as RequestContext
from .context import bind_request as bind_request
from .context import current_request as current_request
from .errors import ApiError as ApiError
from .errors import ApiNotWiredError as ApiNotWiredError
from .errors import ApiRegistrationError as ApiRegistrationError
from .errors import ApiRequestError as ApiRequestError
from .errors import ApiStateError as ApiStateError
from .errors import DuplicateApiMiddlewareError as DuplicateApiMiddlewareError
from .errors import DuplicateExposureError as DuplicateExposureError
from .errors import DuplicateRouteError as DuplicateRouteError
from .errors import InvalidApiHandlerError as InvalidApiHandlerError
from .errors import InvalidErrorMappingError as InvalidErrorMappingError
from .errors import InvalidExposureError as InvalidExposureError
from .errors import NoRequestContextError as NoRequestContextError
from .errors import ResponseCommittedError as ResponseCommittedError
from .exposure import Exposure as Exposure
from .exposure import HttpExposure as HttpExposure
from .exposure import PayloadSource as PayloadSource
from .exposure import default_payload as default_payload
from .exposure import describe_source as describe_source
from .exposure import path_parameters as path_parameters
from .models import ApiHandler as ApiHandler
from .models import ApiMiddleware as ApiMiddleware
from .models import ApiNext as ApiNext
from .models import ErrorResponse as ErrorResponse
from .models import InvalidField as InvalidField
from .models import Invocation as Invocation
from .pipeline import ApiPipeline as ApiPipeline
from .registry import ErrorMap as ErrorMap
from .registry import ErrorMapping as ErrorMapping
from .registry import ExposureRecord as ExposureRecord
from .registry import ExposureRegistry as ExposureRegistry
from .use import register_api_middleware as register_api_middleware
from .use import register_error as register_error
from .use import register_handler as register_handler
from .use import use_api as use_api
