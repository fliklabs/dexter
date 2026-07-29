"""What a handler has to look like, checked when it is registered rather than when it is hit.

Every rejection here happens while wiring. The alternative — discovering that a handler's
`handle` is synchronous, or that its request is not a model, on the first request that reaches
it — turns a typo into a production 500.
"""

from typing import Any, Protocol

import pytest
from pydantic import BaseModel

from dexter.api import InvalidApiHandlerError
from dexter.api._introspection import check_shape, read_contract


class Room(BaseModel):
    room_id: int


class Good:
    async def handle(self, request: Room) -> Room:
        return request


class TestHappyPath:
    def test_reads_the_request_and_response_types(self) -> None:
        assert read_contract(Good) == (Room, Room)

    def test_accepts_a_response_that_is_not_a_model(self) -> None:
        class Listing:
            async def handle(self, request: Room) -> list[str]:
                return []

        assert read_contract(Listing)[1] == list[str]

    def test_accepts_a_handler_that_serves_no_body(self) -> None:
        class Deleting:
            async def handle(self, request: Room) -> None:
                return None

        assert read_contract(Deleting)[1] is type(None)

    def test_accepts_any_parameter_name(self) -> None:
        class Renamed:
            async def handle(self, anything: Room) -> Room:
                return anything

        assert read_contract(Renamed)[0] is Room


class TestShape:
    def test_rejects_something_that_is_not_a_class(self) -> None:
        target: Any = "not a class"
        with pytest.raises(InvalidApiHandlerError, match="must be a class"):
            check_shape(target, "handler")

    def test_rejects_a_protocol(self) -> None:
        class Servable(Protocol):
            async def handle(self, request: Room) -> Room: ...

        with pytest.raises(InvalidApiHandlerError, match="is a Protocol"):
            check_shape(Servable, "handler")

    def test_rejects_a_class_with_no_handle(self) -> None:
        class Empty:
            pass

        with pytest.raises(InvalidApiHandlerError, match="has no `handle` method"):
            check_shape(Empty, "handler")

    def test_rejects_a_synchronous_handle(self) -> None:
        class Blocking:
            def handle(self, request: Room) -> Room:
                return request

        with pytest.raises(InvalidApiHandlerError, match="not asynchronous"):
            check_shape(Blocking, "handler")

    def test_names_what_it_was_asked_to_be(self) -> None:
        class Empty:
            pass

        with pytest.raises(InvalidApiHandlerError, match="cannot be a middleware"):
            check_shape(Empty, "middleware")


class TestArity:
    def test_rejects_a_handle_taking_nothing(self) -> None:
        class Nullary:
            async def handle(self) -> Room:
                return Room(room_id=1)

        with pytest.raises(InvalidApiHandlerError, match="takes nothing"):
            read_contract(Nullary)

    def test_rejects_a_handle_taking_a_context_as_well(self) -> None:
        class Binary:
            async def handle(self, request: Room, context: object) -> Room:
                return request

        with pytest.raises(InvalidApiHandlerError, match="exactly one request"):
            read_contract(Binary)

    def test_points_at_injection_instead(self) -> None:
        class Binary:
            async def handle(self, request: Room, context: object) -> Room:
                return request

        with pytest.raises(InvalidApiHandlerError, match="RequestContext"):
            read_contract(Binary)


class TestRequestModel:
    def test_rejects_an_unannotated_request(self) -> None:
        class Untyped:
            async def handle(self, request) -> Room:  # type: ignore[no-untyped-def]
                return request  # type: ignore[no-any-return]

        with pytest.raises(InvalidApiHandlerError, match="does not annotate"):
            read_contract(Untyped)

    def test_rejects_a_request_that_is_not_a_model(self) -> None:
        class Stringly:
            async def handle(self, request: str) -> Room:
                return Room(room_id=len(request))

        with pytest.raises(InvalidApiHandlerError, match="not a pydantic model"):
            read_contract(Stringly)

    def test_rejects_a_request_naming_something_unimportable(self) -> None:
        class Forward:
            async def handle(self, request: Missing) -> Room:  # type: ignore[name-defined] # noqa: F821
                return Room(room_id=1)

        with pytest.raises(InvalidApiHandlerError, match="does not resolve at runtime"):
            read_contract(Forward)


class TestResponseModel:
    def test_rejects_an_unannotated_response(self) -> None:
        class Untyped:
            async def handle(self, request: Room):  # type: ignore[no-untyped-def]
                return request

        with pytest.raises(InvalidApiHandlerError, match="no return annotation"):
            read_contract(Untyped)

    def test_rejects_a_response_naming_something_unimportable(self) -> None:
        class Forward:
            async def handle(self, request: Room) -> Missing:  # type: ignore[name-defined] # noqa: F821
                return request

        with pytest.raises(InvalidApiHandlerError, match="does not resolve at runtime"):
            read_contract(Forward)
