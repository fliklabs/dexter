"""What `use_api` registers, and what each `register_*` accepts or refuses.

Two conventions are pinned here, both inherited from the rest of dexter. Wiring in the wrong
order names the call that is missing rather than an internal type. And a registry is written
*before* the container is, so a malformed registration is reported by this module's precise
error rather than by whichever container guard would otherwise trip first.
"""

from http import HTTPMethod, HTTPStatus
from typing import Any

import pytest
from pydantic import BaseModel

from dexter.api import (
    ApiNotWiredError,
    ApiPipeline,
    DuplicateApiMiddlewareError,
    DuplicateExposureError,
    DuplicateRouteError,
    ErrorMap,
    Exposure,
    ExposureRegistry,
    HttpExposure,
    InvalidApiHandlerError,
    InvalidErrorMappingError,
    InvalidExposureError,
    RequestContext,
    register_api_middleware,
    register_error,
    register_handler,
    use_api,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import (
    BookRoomHandler,
    GetRoomHandler,
    Inner,
    Outer,
    RoomUnavailableError,
    SearchHandler,
)


def get_rooms(path: str = "/rooms/{room_id}") -> HttpExposure:
    """An exposure for `GetRoomHandler`."""
    return HttpExposure(method=HTTPMethod.GET, path=path)


class TestWhatIsRegistered:
    def test_binds_the_registries_and_the_pipeline_as_instances(
        self, bare_builder: ContainerBuilder
    ) -> None:
        use_api(bare_builder)
        assert isinstance(
            bare_builder.resolve_instance(ExposureRegistry), ExposureRegistry
        )
        assert isinstance(bare_builder.resolve_instance(ApiPipeline), ApiPipeline)
        assert isinstance(bare_builder.resolve_instance(ErrorMap), ErrorMap)

    def test_binds_the_request_context(self, bare_builder: ContainerBuilder) -> None:
        use_api(bare_builder)
        assert bare_builder.is_registered(RequestContext)


class TestWiringOrder:
    def test_registering_a_handler_first_names_the_missing_call(
        self, bare_builder: ContainerBuilder
    ) -> None:
        with pytest.raises(ApiNotWiredError, match="use_api"):
            register_handler(
                bare_builder, GetRoomHandler, get_rooms(), scope=Scope.TRANSIENT
            )

    def test_registering_middleware_first_names_the_missing_call(
        self, bare_builder: ContainerBuilder
    ) -> None:
        with pytest.raises(ApiNotWiredError, match="use_api"):
            register_api_middleware(bare_builder, Outer, scope=Scope.SCOPED)

    def test_registering_an_error_first_names_the_missing_call(
        self, bare_builder: ContainerBuilder
    ) -> None:
        with pytest.raises(ApiNotWiredError, match="use_api"):
            register_error(
                bare_builder, RoomUnavailableError, status=HTTPStatus.NOT_FOUND
            )


class TestHandlers:
    def test_records_the_handler_and_binds_it(self, builder: ContainerBuilder) -> None:
        register_handler(builder, GetRoomHandler, get_rooms(), scope=Scope.TRANSIENT)
        registry = builder.resolve_instance(ExposureRegistry)
        assert registry.is_registered(GetRoomHandler)
        assert builder.is_registered(GetRoomHandler)

    def test_records_the_contract_it_read(self, builder: ContainerBuilder) -> None:
        register_handler(builder, GetRoomHandler, get_rooms(), scope=Scope.TRANSIENT)
        record = builder.resolve_instance(ExposureRegistry).records()[0]
        assert record.request_model.__name__ == "GetRoom"
        assert record.response_model.__name__ == "RoomView"

    def test_accepts_several_exposures_in_one_call(
        self, builder: ContainerBuilder
    ) -> None:
        register_handler(
            builder,
            GetRoomHandler,
            get_rooms(),
            HttpExposure(method=HTTPMethod.HEAD, path="/rooms/{room_id}"),
            scope=Scope.TRANSIENT,
        )
        registry = builder.resolve_instance(ExposureRegistry)
        assert len(registry.of(HttpExposure)) == 2

    def test_rejects_a_handler_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        register_handler(builder, GetRoomHandler, get_rooms(), scope=Scope.TRANSIENT)
        with pytest.raises(DuplicateExposureError, match="already registered"):
            register_handler(
                builder,
                GetRoomHandler,
                get_rooms("/other/{room_id}"),
                scope=Scope.TRANSIENT,
            )

    def test_rejects_a_handler_with_no_exposure(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(InvalidExposureError, match="no exposures"):
            register_handler(builder, GetRoomHandler, scope=Scope.TRANSIENT)

    def test_rejects_two_handlers_claiming_one_route(
        self, builder: ContainerBuilder
    ) -> None:
        register_handler(
            builder, SearchHandler, get_rooms("/rooms"), scope=Scope.TRANSIENT
        )

        class Rival:
            async def handle(self, request: BaseModel) -> str:
                return ""

        with pytest.raises(DuplicateRouteError, match="already served by"):
            register_handler(builder, Rival, get_rooms("/rooms"), scope=Scope.TRANSIENT)

    def test_rejects_a_path_naming_a_field_the_request_does_not_have(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(InvalidExposureError, match="does not declare"):
            register_handler(
                builder,
                GetRoomHandler,
                get_rooms("/rooms/{nonsense}"),
                scope=Scope.TRANSIENT,
            )

    def test_reports_the_module_error_before_the_containers(
        self, builder: ContainerBuilder
    ) -> None:
        """A bad handler is named by `InvalidApiHandlerError`, not by a binding failure."""

        class Blocking:
            def handle(self, request: BaseModel) -> str:
                return ""

        target: Any = Blocking
        with pytest.raises(InvalidApiHandlerError, match="not asynchronous"):
            register_handler(builder, target, get_rooms("/x"), scope=Scope.TRANSIENT)
        assert not builder.is_registered(Blocking)


class TestMiddleware:
    def test_records_order_and_binds_each(self, builder: ContainerBuilder) -> None:
        register_api_middleware(builder, Outer, scope=Scope.SCOPED)
        register_api_middleware(builder, Inner, scope=Scope.SCOPED)
        pipeline = builder.resolve_instance(ApiPipeline)
        assert pipeline.registrations() == (Outer, Inner)
        assert builder.is_registered(Outer)

    def test_rejects_the_same_middleware_twice(self, builder: ContainerBuilder) -> None:
        register_api_middleware(builder, Outer, scope=Scope.SCOPED)
        with pytest.raises(DuplicateApiMiddlewareError, match="would run twice"):
            register_api_middleware(builder, Outer, scope=Scope.SCOPED)

    def test_rejects_middleware_with_a_synchronous_handle(
        self, builder: ContainerBuilder
    ) -> None:
        class Blocking:
            def handle(self, invocation: Any, call_next: Any) -> Any:
                return None

        target: Any = Blocking
        with pytest.raises(InvalidApiHandlerError, match="not asynchronous"):
            register_api_middleware(builder, target, scope=Scope.SCOPED)


class TestErrors:
    def test_records_the_mapping(self, builder: ContainerBuilder) -> None:
        register_error(
            builder, RoomUnavailableError, status=HTTPStatus.CONFLICT, title="Taken"
        )
        mapping = builder.resolve_instance(ErrorMap).mappings()[0]
        assert mapping.status is HTTPStatus.CONFLICT
        assert mapping.title == "Taken"

    def test_rejects_the_same_exception_twice(self, builder: ContainerBuilder) -> None:
        register_error(builder, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        with pytest.raises(InvalidErrorMappingError, match="already mapped"):
            register_error(builder, RoomUnavailableError, status=HTTPStatus.NOT_FOUND)

    def test_rejects_something_that_is_not_an_exception(
        self, builder: ContainerBuilder
    ) -> None:
        target: Any = str
        with pytest.raises(InvalidErrorMappingError, match="only an exception class"):
            register_error(builder, target, status=HTTPStatus.NOT_FOUND)

    def test_binds_nothing_in_the_container(self, builder: ContainerBuilder) -> None:
        """An exception class is a key in a table, not a dependency."""
        register_error(builder, RoomUnavailableError, status=HTTPStatus.CONFLICT)
        assert not builder.is_registered(RoomUnavailableError)


class TestTheProtocolSeam:
    """What a second protocol would cost. Today, nothing but a subclass and a reader."""

    def test_an_exposure_of_another_kind_is_recorded(
        self, builder: ContainerBuilder
    ) -> None:
        class GraphqlExposure(Exposure):
            field: str

        register_handler(
            builder,
            GetRoomHandler,
            get_rooms(),
            GraphqlExposure(field="room"),
            scope=Scope.TRANSIENT,
        )
        registry = builder.resolve_instance(ExposureRegistry)

        assert len(registry.of(GraphqlExposure)) == 1
        assert registry.of(GraphqlExposure)[0][1].field == "room"

    def test_a_transport_sees_only_the_kind_it_asked_for(
        self, builder: ContainerBuilder
    ) -> None:
        class GraphqlExposure(Exposure):
            field: str

        register_handler(
            builder,
            GetRoomHandler,
            get_rooms(),
            GraphqlExposure(field="room"),
            scope=Scope.TRANSIENT,
        )
        registry = builder.resolve_instance(ExposureRegistry)

        assert len(registry.of(HttpExposure)) == 1
        assert [kind for _, kind in registry.of(Exposure)] != []

    def test_a_handler_may_be_registered_with_no_http_exposure_at_all(
        self, builder: ContainerBuilder
    ) -> None:
        class GraphqlExposure(Exposure):
            field: str

        register_handler(
            builder,
            GetRoomHandler,
            GraphqlExposure(field="room"),
            scope=Scope.TRANSIENT,
        )
        registry = builder.resolve_instance(ExposureRegistry)

        assert registry.of(HttpExposure) == ()
        assert registry.is_registered(GetRoomHandler)


class TestRegistryIntrospection:
    def test_reports_registrations_in_order(self, builder: ContainerBuilder) -> None:
        register_handler(builder, GetRoomHandler, get_rooms(), scope=Scope.TRANSIENT)
        register_handler(
            builder,
            BookRoomHandler,
            HttpExposure(method=HTTPMethod.POST, path="/bookings"),
            scope=Scope.TRANSIENT,
        )
        registry = builder.resolve_instance(ExposureRegistry)
        assert [record.handler for record in registry.records()] == [
            GetRoomHandler,
            BookRoomHandler,
        ]

    def test_reports_nothing_for_an_unregistered_handler(
        self, builder: ContainerBuilder
    ) -> None:
        registry = builder.resolve_instance(ExposureRegistry)
        assert not registry.is_registered(GetRoomHandler)
        assert registry.of(HttpExposure) == ()
