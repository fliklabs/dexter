"""Synthesising the signature a web framework reads off an endpoint.

Driven directly, without an application, because the branch that matters is decided before any
request arrives: whether the path names parameters, and therefore whether the request model is
bound whole or split and rebuilt.
"""

from http import HTTPMethod
from typing import Any

import pytest
from fastapi import Request, Response
from pydantic import BaseModel, Field

from dexter.api import HttpExposure, PayloadSource
from dexter.api.http.signature import (
    HTTP_REQUEST,
    HTTP_RESPONSE,
    PAYLOAD,
    build_assembler,
    build_signature,
)

TRANSPORT = (Request, Response)


class Room(BaseModel):
    room_id: int
    verbose: bool = False
    note: str = Field(default="", max_length=4, description="A short note.")


class Flat(BaseModel):
    town: str
    limit: int = 10


def signature_for(path: str, method: HTTPMethod = HTTPMethod.GET) -> Any:
    """The synthesised signature for `Room` at `path`."""
    exposure = HttpExposure(method=method, path=path)
    return build_signature(Room, exposure, TRANSPORT)


class TestAlwaysPresent:
    def test_injects_the_transport_request_and_response(self) -> None:
        signature, _ = signature_for("/rooms")
        assert HTTP_REQUEST in signature.parameters
        assert HTTP_RESPONSE in signature.parameters

    def test_every_parameter_is_keyword_only(self) -> None:
        signature, _ = signature_for("/rooms/{room_id}")
        kinds = {p.kind for p in signature.parameters.values()}
        assert kinds == {signature.parameters[PAYLOAD].kind}

    def test_the_reserved_names_cannot_collide_with_a_field(self) -> None:
        """pydantic forbids a leading underscore in a field name, so these are safe."""
        for name in (HTTP_REQUEST, HTTP_RESPONSE, PAYLOAD):
            assert name.startswith("_")
            assert name not in Room.model_fields


class TestWithoutPathParameters:
    def test_the_payload_is_the_request_model_itself(self) -> None:
        signature, derived = signature_for("/rooms")
        assert derived is None
        assert signature.parameters[PAYLOAD].annotation.__origin__ is Room

    def test_no_field_becomes_its_own_parameter(self) -> None:
        signature, _ = signature_for("/rooms")
        assert set(signature.parameters) == {HTTP_REQUEST, HTTP_RESPONSE, PAYLOAD}


class TestWithPathParameters:
    def test_each_named_field_becomes_its_own_parameter(self) -> None:
        signature, _ = signature_for("/rooms/{room_id}")
        assert "room_id" in signature.parameters

    def test_the_rest_are_carried_by_a_derived_model(self) -> None:
        _, derived = signature_for("/rooms/{room_id}")
        assert derived is not None
        assert set(derived.model_fields) == {"verbose", "note"}

    def test_the_derived_model_is_named_after_the_original(self) -> None:
        _, derived = signature_for("/rooms/{room_id}")
        assert derived is not None
        assert derived.__name__ == "RoomQuery"

    def test_a_body_method_names_the_derived_model_for_a_body(self) -> None:
        _, derived = signature_for("/rooms/{room_id}", HTTPMethod.POST)
        assert derived is not None
        assert derived.__name__ == "RoomBody"

    def test_the_derived_model_keeps_each_fields_constraints(self) -> None:
        _, derived = signature_for("/rooms/{room_id}")
        assert derived is not None
        note = derived.model_fields["note"]
        assert note.description == "A short note."
        assert note.default == ""

    def test_every_field_of_the_model_is_accounted_for(self) -> None:
        _, derived = signature_for("/rooms/{room_id}")
        assert derived is not None
        covered = set(derived.model_fields) | {"room_id"}
        assert covered == set(Room.model_fields)


class TestPayloadSourceDrivesTheMarker:
    @pytest.mark.parametrize(
        ("method", "source"),
        [(HTTPMethod.GET, PayloadSource.QUERY), (HTTPMethod.POST, PayloadSource.BODY)],
    )
    def test_derives_the_source_from_the_method(
        self, method: HTTPMethod, source: PayloadSource
    ) -> None:
        exposure = HttpExposure(method=method, path="/rooms")
        assert exposure.source is source


class TestAssembling:
    def test_returns_the_model_the_framework_already_built(self) -> None:
        exposure = HttpExposure(method=HTTPMethod.GET, path="/rooms")
        assemble = build_assembler(Flat, exposure)
        built = Flat(town="ely", limit=2)
        assert assemble({PAYLOAD: built}) is built

    def test_rebuilds_the_model_from_the_path_and_the_payload(self) -> None:
        exposure = HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}")
        _, derived = build_signature(Room, exposure, TRANSPORT)
        assert derived is not None
        assemble = build_assembler(Room, exposure)

        rebuilt = assemble({"room_id": 7, PAYLOAD: derived(verbose=True, note="hi")})

        assert isinstance(rebuilt, Room)
        assert (rebuilt.room_id, rebuilt.verbose, rebuilt.note) == (7, True, "hi")

    def test_the_rebuild_runs_the_models_own_rules(self) -> None:
        class Checked(BaseModel):
            room_id: int
            nights: int

            def model_post_init(self, context: object) -> None:
                if self.nights < 1:
                    raise ValueError("at least one night")

        exposure = HttpExposure(method=HTTPMethod.POST, path="/stays/{room_id}")
        _, derived = build_signature(Checked, exposure, TRANSPORT)
        assert derived is not None
        assemble = build_assembler(Checked, exposure)

        with pytest.raises(ValueError, match="at least one night"):
            assemble({"room_id": 1, PAYLOAD: derived(nights=0)})
