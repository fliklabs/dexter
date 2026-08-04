"""Authentication in front of real routes, driven over ASGI.

Every test here goes through the whole edge — the framework routes and validates, the request
scope opens, the pipeline runs, the handler's dependencies are built — because that is the only
way to observe the two things that matter most: that a refusal renders as a 401 problem
document rather than a 500 about response validation, and that a refused request builds nothing.
"""

from datetime import timedelta
from typing import Any

import pytest

from dexter.api import RequestContext
from dexter.dependency_injection import ContainerBuilder, ScopeRequiredError
from dexter.iam import NotAuthenticatedError, Principal, TokenService
from dexter.iam.api import (
    Authentication,
    AuthenticationRegistry,
    AuthenticationRequirement,
    current_authentication,
    describe_requirement,
)

from .conftest import (
    ClosedApi,
    Expensive,
    FrozenClock,
    OpenApi,
    bearer,
    make_tokens,
    running,
    serving,
)

PROBLEM = "application/problem+json"


def token_for(subject: str = "a@b.com", **overrides: Any) -> str:
    """An access token the wired application will accept, unless a test spoils the policy."""
    return make_tokens(None, **overrides).mint(Principal(subject=subject)).access_token


class TestAnOpenRoute:
    async def test_serves_a_request_carrying_no_token(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            response = await client.get("/open")

        assert response.status_code == 200
        assert response.json()["subject"] == "anonymous"

    async def test_still_reads_a_token_that_is_there(
        self, routes: ContainerBuilder
    ) -> None:
        """A route that does not require a caller still knows one when it sees one."""
        async with serving(routes) as client:
            response = await client.get("/open", headers=bearer(token_for()))

        assert response.json()["subject"] == "a@b.com"


class TestAClosedRoute:
    async def test_refuses_a_request_carrying_no_token(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            response = await client.get("/closed")

        assert response.status_code == 401
        assert response.headers["content-type"].startswith(PROBLEM)
        assert response.json()["title"] == "Not authenticated"

    async def test_serves_a_request_carrying_a_valid_token(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            response = await client.get("/closed", headers=bearer(token_for()))

        assert response.status_code == 200
        assert response.json()["subject"] == "a@b.com"

    async def test_accepts_a_token_carrying_claims(
        self, routes: ContainerBuilder
    ) -> None:
        token = make_tokens().mint(Principal.of("a@b.com", role="admin")).access_token

        async with serving(routes) as client:
            response = await client.get("/closed", headers=bearer(token))

        assert response.status_code == 200

    async def test_refuses_an_expired_token_as_expired(
        self, routes: ContainerBuilder, clock: FrozenClock
    ) -> None:
        """Distinct from an invalid one: the client should refresh, not log in again."""
        token = token_for()
        clock.advance(timedelta(hours=1))

        async with serving(routes) as client:
            response = await client.get("/closed", headers=bearer(token))

        assert response.status_code == 401
        assert response.json()["title"] == "Expired token"

    async def test_refuses_a_refresh_token_presented_as_a_bearer_credential(
        self, routes: ContainerBuilder
    ) -> None:
        pair = make_tokens().mint(Principal(subject="a@b.com"))

        async with serving(routes) as client:
            response = await client.get("/closed", headers=bearer(pair.refresh_token))

        assert response.status_code == 401
        assert response.json()["title"] == "Wrong token kind"


class TestABadToken:
    @pytest.mark.parametrize(
        "header",
        [
            "",
            "Bearer",
            "Bearer ",
            "Basic abcdef",
            "not-even-a-scheme",
            "Bearer not-a-token",
        ],
        ids=[
            "empty",
            "scheme with nothing after it",
            "scheme with blank credential",
            "another scheme",
            "no scheme at all",
            "text that is not a token",
        ],
    )
    async def test_is_refused_on_an_open_route_too(
        self, routes: ContainerBuilder, header: str
    ) -> None:
        """Absence is an ordinary state; garbage is a caller doing something wrong."""
        async with serving(routes) as client:
            response = await client.get("/open", headers={"Authorization": header})

        assert response.status_code == 401

    async def test_a_token_signed_with_another_key_is_refused(
        self, routes: ContainerBuilder
    ) -> None:
        theirs = token_for(secret="a-different-key-that-is-long-enough-here")

        async with serving(routes) as client:
            response = await client.get("/closed", headers=bearer(theirs))

        assert response.status_code == 401
        assert response.json()["title"] == "Invalid token"

    async def test_the_scheme_is_matched_case_insensitively(
        self, routes: ContainerBuilder
    ) -> None:
        """RFC 7235 says it is, and clients disagree about how to spell it."""
        async with serving(routes) as client:
            response = await client.get(
                "/closed", headers={"Authorization": f"bearer {token_for()}"}
            )

        assert response.status_code == 200

    async def test_the_header_name_is_matched_case_insensitively(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            response = await client.get(
                "/closed", headers={"AUTHORIZATION": f"Bearer {token_for()}"}
            )

        assert response.status_code == 200


class TestNothingIsBuiltForARefusedRequest:
    async def test_a_refusal_never_constructs_the_handler_or_its_dependencies(
        self, routes: ContainerBuilder, built: list[Expensive]
    ) -> None:
        """A request turned away costs no database connection."""
        async with serving(routes) as client:
            response = await client.get("/costly")

        assert response.status_code == 401
        assert built == []

    async def test_an_allowed_request_does_build_them(
        self, routes: ContainerBuilder, built: list[Expensive]
    ) -> None:
        """The negative test above would pass if the route were simply broken."""
        async with serving(routes) as client:
            response = await client.get("/costly", headers=bearer(token_for()))

        assert response.status_code == 200
        assert len(built) == 1


class TestDeclaringAPrincipalIsItselfARequirement:
    async def test_an_open_route_whose_handler_asks_for_one_answers_401(
        self, routes: ContainerBuilder
    ) -> None:
        """The annotation does real work: no rule was registered for this handler."""
        async with serving(routes) as client:
            response = await client.get("/demanding")

        assert response.status_code == 401
        assert response.json()["title"] == "Not authenticated"

    async def test_and_serves_the_request_when_there_is_a_caller(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            response = await client.get("/demanding", headers=bearer(token_for()))

        assert response.status_code == 200


class TestResolvingTheCaller:
    async def test_authentication_is_scoped_to_the_request(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        try:
            with pytest.raises(ScopeRequiredError):
                await container.resolve(Authentication)
        finally:
            await container.aclose()

    async def test_authentication_names_nobody_outside_the_pipeline(
        self, builder: ContainerBuilder
    ) -> None:
        """A handler exercised directly in a test has not been authenticated by anything."""
        async with running(builder):
            context = RequestContext(handler=OpenApi)
            assert current_authentication(context).principal is None

    async def test_each_request_sees_its_own_caller(
        self, routes: ContainerBuilder
    ) -> None:
        async with serving(routes) as client:
            first = await client.get("/closed", headers=bearer(token_for("one@x.com")))
            second = await client.get("/closed", headers=bearer(token_for("two@x.com")))

        assert first.json()["subject"] == "one@x.com"
        assert second.json()["subject"] == "two@x.com"

    def test_require_raises_when_there_is_nobody(self) -> None:
        with pytest.raises(NotAuthenticatedError):
            Authentication().require()

    def test_require_returns_the_caller_when_there_is_one(self) -> None:
        principal = Principal(subject="a@b.com")

        assert Authentication(principal).require() is principal

    def test_is_authenticated_reports_whether_anybody_was_named(self) -> None:
        assert not Authentication().is_authenticated
        assert Authentication(Principal(subject="a@b.com")).is_authenticated

    def test_repr_names_the_subject(self) -> None:
        assert "a@b.com" in repr(Authentication(Principal(subject="a@b.com")))
        assert "None" in repr(Authentication())


class TestTheRegistry:
    def test_a_handler_nobody_named_is_anonymous(self) -> None:
        registry = AuthenticationRegistry()

        assert registry.requirement_for(OpenApi) is AuthenticationRequirement.ANONYMOUS
        assert not registry.requires(OpenApi)

    def test_a_named_handler_is_required(self) -> None:
        registry = AuthenticationRegistry()
        registry.require(ClosedApi)

        assert registry.requirement_for(ClosedApi) is AuthenticationRequirement.REQUIRED
        assert registry.requires(ClosedApi)

    def test_lists_every_rule_so_the_default_is_auditable(self) -> None:
        registry = AuthenticationRegistry()
        registry.require(ClosedApi)

        assert registry.requirements() == (
            (ClosedApi, AuthenticationRequirement.REQUIRED),
        )

    def test_repr_counts_the_rules(self) -> None:
        registry = AuthenticationRegistry()
        registry.require(ClosedApi)

        assert "required=1" in repr(registry)

    def test_describes_a_requirement_as_the_symbol_a_reader_would_type(self) -> None:
        assert (
            describe_requirement(AuthenticationRequirement.REQUIRED)
            == "AuthenticationRequirement.REQUIRED"
        )


class TestTheTokenServiceIsResolvable:
    async def test_the_container_builds_one_from_the_registered_policy(
        self, builder: ContainerBuilder
    ) -> None:
        async with running(builder) as scope:
            tokens = await scope.resolve(TokenService)

        assert (
            tokens.verify_access(
                tokens.mint(Principal(subject="a@b.com")).access_token
            ).subject
            == "a@b.com"
        )
