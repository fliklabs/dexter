"""Wiring: what each `use_*` binds, what order they have to run in, and how it fails."""

from http import HTTPStatus

import pytest

from dexter.api import ApiNotWiredError, ApiPipeline, ErrorMap, use_api
from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
    ResolutionError,
)
from dexter.iam import (
    Clock,
    DuplicateAuthenticationRuleError,
    IamNotWiredError,
    InMemoryMagicCodeStore,
    MagicCodeService,
    MagicCodeStore,
    Principal,
    SystemClock,
    TokenService,
    register_magic_code_policy,
    register_token_policy,
    use_iam,
    use_in_memory_magic_codes,
)
from dexter.iam.api import (
    Authentication,
    AuthenticationMiddleware,
    AuthenticationRegistry,
    require_authentication,
    use_authentication,
)

from .conftest import (
    ClosedApi,
    FrozenClock,
    OpenApi,
    make_code_policy,
    make_token_policy,
    running,
)


class TestUseIam:
    async def test_binds_a_system_clock_by_default(self) -> None:
        builder = ContainerBuilder()
        use_iam(builder)

        async with running(builder) as scope:
            assert isinstance(await scope.resolve(Clock), SystemClock)

    async def test_leaves_an_application_s_own_clock_alone(self) -> None:
        """The one conditional binding in the module, and the reason it exists."""
        clock = FrozenClock()
        builder = ContainerBuilder()
        builder.register(Clock).to_instance(clock)
        use_iam(builder)

        async with running(builder) as scope:
            assert await scope.resolve(Clock) is clock

    async def test_a_token_service_needs_a_policy_and_says_so(self) -> None:
        builder = ContainerBuilder()
        use_iam(builder)

        async with running(builder) as scope:
            with pytest.raises(ResolutionError, match="TokenPolicy"):
                await scope.resolve(TokenService)

    async def test_a_magic_code_service_needs_a_store_and_says_so(self) -> None:
        builder = ContainerBuilder()
        use_iam(builder)
        register_magic_code_policy(builder, make_code_policy())

        async with running(builder) as scope:
            with pytest.raises(ResolutionError, match="MagicCodeStore"):
                await scope.resolve(MagicCodeService)

    async def test_the_token_service_is_one_object(self) -> None:
        builder = ContainerBuilder()
        use_iam(builder)
        register_token_policy(builder, make_token_policy())

        async with running(builder) as scope:
            assert await scope.resolve(TokenService) is await scope.resolve(
                TokenService
            )


class TestUseInMemoryMagicCodes:
    async def test_both_keys_name_one_store(self) -> None:
        """Two bindings of the class would write codes to one dictionary and read another."""
        builder = ContainerBuilder()
        use_iam(builder)
        use_in_memory_magic_codes(builder)
        register_magic_code_policy(builder, make_code_policy())

        async with running(builder) as scope:
            assert await scope.resolve(MagicCodeStore) is await scope.resolve(
                InMemoryMagicCodeStore
            )

    async def test_the_service_writes_where_the_test_can_read(self) -> None:
        builder = ContainerBuilder()
        use_iam(builder)
        use_in_memory_magic_codes(builder)
        register_magic_code_policy(builder, make_code_policy())

        async with running(builder) as scope:
            codes = await scope.resolve(MagicCodeService)
            store = await scope.resolve(InMemoryMagicCodeStore)
            await codes.issue("someone@example.com")

        assert len(store) == 1

    def test_a_second_store_is_refused(self) -> None:
        """Choosing a store twice would leave which one wins to registration order."""
        builder = ContainerBuilder()
        use_in_memory_magic_codes(builder)

        with pytest.raises(DuplicateRegistrationError):
            use_in_memory_magic_codes(builder)


class TestUseAuthentication:
    def test_needs_the_api_wired_first(self) -> None:
        """The pipeline the middleware joins is created by `use_api`."""
        builder = ContainerBuilder()

        with pytest.raises(ApiNotWiredError, match="use_api"):
            use_authentication(builder)

    def test_registers_the_middleware_in_the_pipeline(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)
        use_authentication(builder)

        pipeline = builder.resolve_instance(ApiPipeline)

        assert AuthenticationMiddleware in pipeline.registrations()

    def test_maps_every_refusal_to_401(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)
        use_authentication(builder)

        statuses = {
            mapping.status for mapping in builder.resolve_instance(ErrorMap).mappings()
        }

        assert statuses == {HTTPStatus.UNAUTHORIZED}

    async def test_binds_the_caller_and_the_outcome(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)
        use_authentication(builder)

        assert builder.is_registered(Authentication)
        assert builder.is_registered(Principal)
        assert builder.is_registered(AuthenticationRegistry)


class TestRequireAuthentication:
    def test_needs_use_authentication_first(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)

        with pytest.raises(IamNotWiredError, match="use_authentication"):
            require_authentication(builder, ClosedApi)

    def test_names_the_missing_call_rather_than_an_internal_type(self) -> None:
        builder = ContainerBuilder()

        with pytest.raises(IamNotWiredError) as raised:
            require_authentication(builder, ClosedApi)

        assert "use_authentication(builder)" in str(raised.value)

    def test_refuses_a_second_rule_for_one_handler(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)
        use_authentication(builder)
        require_authentication(builder, ClosedApi)

        with pytest.raises(DuplicateAuthenticationRuleError):
            require_authentication(builder, ClosedApi)

    def test_records_the_rule_where_the_middleware_will_read_it(self) -> None:
        builder = ContainerBuilder()
        use_api(builder)
        use_authentication(builder)
        require_authentication(builder, ClosedApi)

        registry = builder.resolve_instance(AuthenticationRegistry)

        assert registry.requires(ClosedApi)
        assert not registry.requires(OpenApi)


class TestTheSystemClock:
    def test_reports_an_aware_instant(self) -> None:
        """A naive datetime compares unpredictably, and would fail exactly at expiry."""
        assert SystemClock().now().tzinfo is not None

    def test_repr_says_what_it_is(self) -> None:
        assert repr(SystemClock()) == "SystemClock()"
