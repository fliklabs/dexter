"""Serving requests, end to end, over ASGI in this process.

Both branches of the endpoint construction are exercised here, because they are the part of
the module a reader cannot verify by reading a signature: a path with no parameters binds the
request model whole, and a path with parameters splits it and puts it back together.
"""

from http import HTTPMethod, HTTPStatus

from fastapi import FastAPI
from pydantic import BaseModel, Field

from dexter.api import (
    Cookie,
    HttpExposure,
    PayloadSource,
    RequestContext,
    register_handler,
)
from dexter.dependency_injection import ContainerBuilder, Scope

from .conftest import serving


class TestReading:
    async def test_binds_a_path_parameter_and_a_query_flag(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.get("/rooms/12", params={"verbose": "true"})
        assert response.status_code == HTTPStatus.OK
        assert response.json() == {"room_id": 12, "verbose": True, "tenant": ""}

    async def test_applies_a_field_default_when_the_query_omits_it(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.get("/rooms/12")
        assert response.json()["verbose"] is False

    async def test_binds_a_whole_model_from_the_query_when_the_path_is_literal(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.get("/rooms", params={"town": "ely", "limit": 2})
        assert response.json() == ["ely-0", "ely-1"]

    async def test_coerces_a_path_parameter_to_the_declared_type(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.get("/rooms/12")
        assert response.json()["room_id"] == 12


class TestWriting:
    async def test_binds_a_body(self, rooms: ContainerBuilder) -> None:
        async with serving(rooms) as client:
            response = await client.post("/bookings", json={"room_id": 3, "nights": 2})
        assert response.json() == {"reference": "BK-3-2"}

    async def test_serves_the_status_the_exposure_declared(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.post("/bookings", json={"room_id": 3, "nights": 2})
        assert response.status_code == HTTPStatus.CREATED

    async def test_a_handler_can_set_a_response_header(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.post("/bookings", json={"room_id": 3, "nights": 2})
        assert response.headers["location"] == "/rooms/3"

    async def test_a_handler_can_override_the_declared_status(
        self, builder: ContainerBuilder
    ) -> None:
        class Maybe(BaseModel):
            created: bool = True

        class MaybeHandler:
            def __init__(self, context: RequestContext) -> None:
                self.context = context

            async def handle(self, request: Maybe) -> str:
                if not request.created:
                    self.context.set_status(HTTPStatus.ACCEPTED)
                return "done"

        register_handler(
            builder,
            MaybeHandler,
            HttpExposure(
                method=HTTPMethod.POST, path="/maybe", status=HTTPStatus.CREATED
            ),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            declared = await client.post("/maybe", json={"created": True})
            overridden = await client.post("/maybe", json={"created": False})

        assert declared.status_code == HTTPStatus.CREATED
        assert overridden.status_code == HTTPStatus.ACCEPTED

    async def test_a_handler_can_set_a_cookie(self, builder: ContainerBuilder) -> None:
        class SignIn(BaseModel):
            who: str

        class SignInHandler:
            def __init__(self, context: RequestContext) -> None:
                self.context = context

            async def handle(self, request: SignIn) -> str:
                self.context.set_cookie(
                    Cookie(
                        name="session",
                        value=request.who,
                        http_only=True,
                        max_age=60,
                        same_site="strict",
                    )
                )
                return request.who

        register_handler(
            builder,
            SignInHandler,
            HttpExposure(method=HTTPMethod.POST, path="/sign-in"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.post("/sign-in", json={"who": "ada"})

        assert response.cookies["session"] == "ada"
        assert "HttpOnly" in response.headers["set-cookie"]

    async def test_a_handler_reads_a_cookie_the_caller_sent(
        self, builder: ContainerBuilder
    ) -> None:
        class Whoami(BaseModel):
            pass

        class WhoamiHandler:
            def __init__(self, context: RequestContext) -> None:
                self.context = context

            async def handle(self, request: Whoami) -> str:
                return self.context.cookies.get("session", "anonymous")

        register_handler(
            builder,
            WhoamiHandler,
            HttpExposure(method=HTTPMethod.GET, path="/whoami"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            # Sent as a header rather than through the client's cookie jar: it is what the
            # server actually receives, and httpx deprecates per-request cookies.
            signed = await client.get("/whoami", headers={"cookie": "session=ada"})
            anonymous = await client.get("/whoami")

        assert signed.json() == "ada"
        assert anonymous.json() == "anonymous"


class TestValidation:
    async def test_a_bad_path_parameter_names_the_field(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            response = await client.get("/rooms/not-a-number")
        assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
        assert response.json()["errors"][0]["location"] == ["path", "room_id"]

    async def test_a_bad_body_names_the_field(self, rooms: ContainerBuilder) -> None:
        async with serving(rooms) as client:
            response = await client.post("/bookings", json={"room_id": 3})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
        assert response.json()["errors"][0]["location"] == ["body", "nights"]

    async def test_a_rule_on_the_whole_model_still_runs_when_the_path_splits_it(
        self, builder: ContainerBuilder
    ) -> None:
        """A derived model cannot carry a model-level validator, so the real one is rebuilt."""

        class Stay(BaseModel):
            room_id: int
            checkout: int

            def model_post_init(self, context: object) -> None:
                if self.checkout <= self.room_id:
                    raise ValueError("checkout must be later")

        class StayHandler:
            async def handle(self, request: Stay) -> int:
                return request.checkout

        register_handler(
            builder,
            StayHandler,
            HttpExposure(method=HTTPMethod.GET, path="/stays/{room_id}"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            good = await client.get("/stays/1", params={"checkout": 5})
            bad = await client.get("/stays/9", params={"checkout": 5})

        assert good.status_code == HTTPStatus.OK
        assert bad.status_code == HTTPStatus.UNPROCESSABLE_CONTENT


class TestNoBody:
    async def test_a_handler_serving_nothing_can_use_a_bodyless_status(
        self, builder: ContainerBuilder
    ) -> None:
        """`-> None` with 204 is the obvious thing to write, so it has to work.

        Passing the annotation through as a response model would describe a body, and the
        framework refuses to pair that with a status that forbids one.
        """

        class Forget(BaseModel):
            room_id: int

        class ForgetHandler:
            async def handle(self, request: Forget) -> None:
                return None

        register_handler(
            builder,
            ForgetHandler,
            HttpExposure(
                method=HTTPMethod.DELETE,
                path="/rooms/{room_id}",
                status=HTTPStatus.NO_CONTENT,
            ),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.delete("/rooms/1")

        assert response.status_code == HTTPStatus.NO_CONTENT
        assert response.text == ""

    async def test_a_handler_serving_nothing_still_works_with_a_normal_status(
        self, builder: ContainerBuilder
    ) -> None:
        class Ping(BaseModel):
            pass

        class PingHandler:
            async def handle(self, request: Ping) -> None:
                return None

        register_handler(
            builder,
            PingHandler,
            HttpExposure(method=HTTPMethod.GET, path="/ping"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.get("/ping")

        assert response.status_code == HTTPStatus.OK
        assert response.json() is None


class TestSchema:
    async def test_documents_path_and_query_parameters_separately(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            schema = (await client.get("/openapi.json")).json()
        parameters = schema["paths"]["/rooms/{room_id}"]["get"]["parameters"]
        assert [(p["name"], p["in"]) for p in parameters] == [
            ("room_id", "path"),
            ("verbose", "query"),
        ]

    async def test_carries_the_tags_the_exposure_declared(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            schema = (await client.get("/openapi.json")).json()
        assert schema["paths"]["/rooms/{room_id}"]["get"]["tags"] == ["rooms"]

    async def test_documents_the_handler_with_its_own_docstring(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            schema = (await client.get("/openapi.json")).json()
        assert (
            schema["paths"]["/rooms/{room_id}"]["get"]["description"]
            == "Describe one room."
        )

    async def test_names_the_body_schema_after_the_request_model(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            schema = (await client.get("/openapi.json")).json()
        body = schema["paths"]["/bookings"]["post"]["requestBody"]
        assert body["content"]["application/json"]["schema"]["$ref"].endswith(
            "/BookRoom"
        )

    async def test_carries_a_field_constraint_into_the_schema(
        self, builder: ContainerBuilder
    ) -> None:
        class Paged(BaseModel):
            page: int = Field(default=1, le=50, description="Which page.")

        class PagedHandler:
            async def handle(self, request: Paged) -> int:
                return request.page

        register_handler(
            builder,
            PagedHandler,
            HttpExposure(method=HTTPMethod.GET, path="/paged"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            schema = (await client.get("/openapi.json")).json()
        parameter = schema["paths"]["/paged"]["get"]["parameters"][0]
        assert parameter["schema"]["maximum"] == 50
        assert parameter["description"] == "Which page."

    async def test_carries_a_path_field_constraint_into_the_schema(
        self, builder: ContainerBuilder
    ) -> None:
        class Numbered(BaseModel):
            room_id: int = Field(gt=0, description="The room's number.")

        class NumberedHandler:
            async def handle(self, request: Numbered) -> int:
                return request.room_id

        register_handler(
            builder,
            NumberedHandler,
            HttpExposure(method=HTTPMethod.GET, path="/numbered/{room_id}"),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            rejected = await client.get("/numbered/0")
            schema = (await client.get("/openapi.json")).json()

        assert rejected.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
        parameter = schema["paths"]["/numbered/{room_id}"]["get"]["parameters"][0]
        assert parameter["schema"]["exclusiveMinimum"] == 0
        assert parameter["description"] == "The room's number."

    async def test_omits_a_route_that_asked_to_be_hidden(
        self, builder: ContainerBuilder
    ) -> None:
        class Hidden(BaseModel):
            pass

        class HiddenHandler:
            async def handle(self, request: Hidden) -> str:
                return "ok"

        register_handler(
            builder,
            HiddenHandler,
            HttpExposure(
                method=HTTPMethod.GET, path="/internal", include_in_schema=False
            ),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            schema = (await client.get("/openapi.json")).json()
            assert (await client.get("/internal")).status_code == HTTPStatus.OK
        assert "/internal" not in schema["paths"]


class TestMounting:
    async def test_a_prefix_is_prepended_to_every_path(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms, prefix="/api/v1") as client:
            assert (await client.get("/api/v1/rooms/1")).status_code == HTTPStatus.OK
            assert (await client.get("/rooms/1")).status_code == HTTPStatus.NOT_FOUND

    async def test_mounts_onto_an_application_the_consumer_supplied(
        self, rooms: ContainerBuilder
    ) -> None:
        app = FastAPI(title="Front desk")

        @app.get("/health")
        async def health() -> str:
            return "ok"

        async with serving(rooms, app=app) as client:
            assert (await client.get("/health")).status_code == HTTPStatus.OK
            assert (await client.get("/rooms/1")).status_code == HTTPStatus.OK
            assert (await client.get("/openapi.json")).json()["info"][
                "title"
            ] == "Front desk"

    async def test_an_unrouted_path_is_the_frameworks_own_not_found(
        self, rooms: ContainerBuilder
    ) -> None:
        async with serving(rooms) as client:
            assert (await client.get("/nothing")).status_code == HTTPStatus.NOT_FOUND


class TestPayloadOverride:
    async def test_a_bodyless_method_can_be_told_to_read_a_body(
        self, builder: ContainerBuilder
    ) -> None:
        class Filter(BaseModel):
            town: str

        class FilterHandler:
            async def handle(self, request: Filter) -> str:
                return request.town

        register_handler(
            builder,
            FilterHandler,
            HttpExposure(
                method=HTTPMethod.GET, path="/filter", payload=PayloadSource.BODY
            ),
            scope=Scope.TRANSIENT,
        )
        async with serving(builder) as client:
            response = await client.request("GET", "/filter", json={"town": "ely"})
        assert response.json() == "ely"
