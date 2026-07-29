"""Declaring a way in, and what a malformed one reports.

A path is checked when the exposure is *constructed*, not when it is registered, so the
traceback points at the line in the consumer's own wiring where the mistake was written.
"""

from http import HTTPMethod, HTTPStatus
from typing import Any

import pytest

from dexter.api import (
    HttpExposure,
    InvalidExposureError,
    PayloadSource,
    default_payload,
    describe_source,
    path_parameters,
)


class TestPathParameters:
    def test_finds_every_placeholder_in_order(self) -> None:
        assert path_parameters("/towns/{town}/rooms/{room_id}") == ("town", "room_id")

    def test_finds_none_in_a_literal_path(self) -> None:
        assert path_parameters("/rooms") == ()

    def test_tolerates_a_converter_suffix(self) -> None:
        assert path_parameters("/rooms/{room_id:int}") == ("room_id",)


class TestPayloadSource:
    @pytest.mark.parametrize(
        "method",
        [
            HTTPMethod.GET,
            HTTPMethod.HEAD,
            HTTPMethod.DELETE,
            HTTPMethod.OPTIONS,
            HTTPMethod.TRACE,
        ],
    )
    def test_a_bodyless_method_reads_the_query_string(self, method: HTTPMethod) -> None:
        assert default_payload(method) is PayloadSource.QUERY

    @pytest.mark.parametrize(
        "method", [HTTPMethod.POST, HTTPMethod.PUT, HTTPMethod.PATCH]
    )
    def test_a_method_with_a_body_reads_it(self, method: HTTPMethod) -> None:
        assert default_payload(method) is PayloadSource.BODY

    def test_an_exposure_derives_it_from_the_method(self) -> None:
        exposure = HttpExposure(method=HTTPMethod.GET, path="/rooms")
        assert exposure.source is PayloadSource.QUERY

    def test_an_exposure_may_override_it(self) -> None:
        exposure = HttpExposure(
            method=HTTPMethod.GET, path="/rooms", payload=PayloadSource.BODY
        )
        assert exposure.source is PayloadSource.BODY

    def test_is_rendered_as_the_symbol_a_caller_would_type(self) -> None:
        assert describe_source(PayloadSource.QUERY) == "PayloadSource.QUERY"

    def test_value_equals_name(self) -> None:
        for member in PayloadSource:
            assert member.value == member.name


class TestGuards:
    def test_rejects_a_relative_path(self) -> None:
        with pytest.raises(InvalidExposureError, match="must start with"):
            HttpExposure(method=HTTPMethod.GET, path="rooms")

    def test_rejects_an_unbalanced_brace(self) -> None:
        with pytest.raises(InvalidExposureError, match="unbalanced"):
            HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id")

    def test_rejects_a_placeholder_named_twice(self) -> None:
        with pytest.raises(InvalidExposureError, match="more than once"):
            HttpExposure(method=HTTPMethod.GET, path="/a/{id}/b/{id}")


class TestDefaults:
    def test_defaults_to_two_hundred(self) -> None:
        exposure = HttpExposure(method=HTTPMethod.GET, path="/rooms")
        assert exposure.status is HTTPStatus.OK

    def test_is_frozen_and_hashable(self) -> None:
        exposure = HttpExposure(method=HTTPMethod.GET, path="/rooms", tags=("a",))
        assert hash(exposure) is not None

    def test_rejects_an_unknown_field(self) -> None:
        # Bound to an `Any` local rather than suppressed: `warn_unused_ignores` would flag a
        # `# type: ignore` here the moment the signature changed.
        exposure: Any = HttpExposure
        with pytest.raises(ValueError, match="Extra inputs"):
            exposure(method=HTTPMethod.GET, path="/rooms", nonsense=1)
