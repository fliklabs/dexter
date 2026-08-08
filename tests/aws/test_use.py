"""Covers `dexter.aws.session` and `dexter.aws.use`: construction, and what wiring produces.

The scope assertions are the substance. `SecretsManagerClient` holds the secret cache, so a
transient binding hands every caller an empty one — which does not fail, it just quietly fetches
every time and puts the module back where it started.
"""

from collections.abc import Callable
from typing import Any, Protocol

import pytest

from dexter.aws import (
    AwsConfig,
    AwsEndpoints,
    AwsSession,
    AwsWiringError,
    CredentialsUnavailableError,
    ParameterStoreClient,
    ParameterValue,
    RetryMode,
    S3Client,
    SecretsManagerClient,
    SecretValue,
    SesClient,
    SnsClient,
    SqsClient,
    StaticValue,
    ValueSource,
    register_aws_config,
    register_parameter_value,
    register_secret_value,
    use_aws,
)
from dexter.dependency_injection import (
    CaptiveDependencyError,
    Container,
    ContainerBuilder,
    Scope,
    UnregisteredDependencyError,
)

from .conftest import REGION, SECRET_ID


class DatabasePassword(ValueSource, Protocol):
    """An application's own marker for one configured value.

    Three lines in the consumer's own vocabulary is the whole of what the provider pattern asks
    them to write, so the tests declare one exactly as an application would.
    """


class OrdersTableName(ValueSource, Protocol):
    """A second marker, to prove two are told apart."""


def settings_of(client: Any) -> Any:
    """The botocore `Config` a client was built with, as an untyped value.

    `Config` sets every one of its options dynamically in `__init__`, so the stubs declare none
    of them and `config.retries` is an attribute error to mypy. Reading it through an `Any` is
    the repo convention for a deliberately unchecked access — a `# type: ignore` would be flagged
    by `warn_unused_ignores` the day the stubs improve.
    """
    return client.meta.config


def wire(*, with_config: bool = True) -> Container:
    """A container with the AWS module in it."""
    builder = ContainerBuilder()
    use_aws(builder)
    if with_config:
        register_aws_config(builder, AwsConfig(region=REGION))
    return builder.build()


def wire_value(register: Callable[[ContainerBuilder], None], /) -> Container:
    """A wired container with one value registration applied.

    What the marker resolves *to* is asserted here; what it fetches is asserted in the client's
    own test module against a stubbed client. Splitting them is deliberate — the container builds
    its own `AwsSession`, so a stub installed on the fixture's session would not be the one the
    resolved value ends up holding, and a test that appeared to prove a fetch would be proving
    nothing.
    """
    builder = ContainerBuilder()
    use_aws(builder)
    register_aws_config(builder, AwsConfig(region=REGION))
    register(builder)
    return builder.build()


class TestConfig:
    def test_rejects_a_blank_region(self) -> None:
        """Caught here rather than as a `NoRegionError` from the first call that signs."""
        with pytest.raises(ValueError, match="region"):
            AwsConfig(region="   ")

    def test_rejects_an_unknown_field(self) -> None:
        # Bound to an `Any` local rather than silenced with `# type: ignore`, which
        # `warn_unused_ignores` would then flag. The repo convention for a deliberately
        # wrong type.
        any_kwargs: Any = {"region": REGION, "regoin": "typo"}
        with pytest.raises(ValueError, match="regoin"):
            AwsConfig(**any_kwargs)

    def test_is_frozen(self) -> None:
        # No `# type: ignore` here: the pydantic mypy plugin is not enabled in this repo, so
        # mypy sees an ordinary attribute assignment and `warn_unused_ignores` would flag a
        # suppression for an error it never raised. The guard is pydantic's, at runtime.
        config = AwsConfig(region=REGION)
        with pytest.raises(ValueError, match="frozen"):
            config.region = "us-east-1"


class TestSession:
    def test_builds_a_client_for_each_service_in_the_configured_region(self) -> None:
        session = AwsSession(AwsConfig(region=REGION))

        assert session.s3.meta.region_name == REGION
        assert session.dynamodb.meta.region_name == REGION
        assert session.secrets.meta.region_name == REGION
        assert session.ses.meta.region_name == REGION
        assert session.sns.meta.region_name == REGION
        assert session.sqs.meta.region_name == REGION
        assert session.ssm.meta.region_name == REGION

    def test_honours_a_default_endpoint_override(self) -> None:
        """What lets a test drive a local stand-in through the real boto3 request path."""
        session = AwsSession(
            AwsConfig(
                region=REGION, endpoints=AwsEndpoints(default="http://localhost:4566")
            )
        )
        assert session.s3.meta.endpoint_url == "http://localhost:4566"
        assert session.dynamodb.meta.endpoint_url == "http://localhost:4566"

    def test_a_service_endpoint_wins_over_the_default(self) -> None:
        """The setup a single `endpoint_url` could not express: two stand-ins, two ports."""
        session = AwsSession(
            AwsConfig(
                region=REGION,
                endpoints=AwsEndpoints(
                    default="http://localhost:4566", dynamodb="http://localhost:8000"
                ),
            )
        )
        assert session.dynamodb.meta.endpoint_url == "http://localhost:8000"
        assert session.s3.meta.endpoint_url == "http://localhost:4566"

    def test_no_endpoint_at_all_reaches_the_real_service(self) -> None:
        session = AwsSession(AwsConfig(region=REGION))
        assert "amazonaws.com" in session.s3.meta.endpoint_url

    def test_only_s3_is_signed_with_the_s3_signature_version(self) -> None:
        """**The correction that matters at seven clients.**

        botocore applies `Config.signature_version` to whichever service is being built without
        checking that the two agree, so a shared config hands `S3SigV4Auth` — which skips path
        normalisation and injects an S3-specific payload header — to DynamoDB and SQS as well.
        It tends to verify anyway, which is what makes the mistake survivable and therefore
        durable, so it is asserted rather than trusted.
        """
        session = AwsSession(AwsConfig(region=REGION))

        assert settings_of(session.s3).signature_version == "s3v4"
        for other in (session.dynamodb, session.secrets, session.sqs, session.ssm):
            assert settings_of(other).signature_version != "s3v4"

    def test_every_client_carries_the_configured_retries_and_timeouts(self) -> None:
        session = AwsSession(
            AwsConfig(
                region=REGION,
                max_attempts=7,
                retry_mode=RetryMode.ADAPTIVE,
                connect_timeout_seconds=1.5,
                read_timeout_seconds=2.5,
            )
        )

        for client in (session.s3, session.sqs):
            settings = settings_of(client)
            # `total_max_attempts`, not botocore's `max_attempts` — that one counts retries
            # after the first try, so configuring it with 7 would buy 8 attempts. Asserting the
            # exact dictionary is what catches a change back to the misleading spelling.
            assert settings.retries == {"mode": "adaptive", "total_max_attempts": 7}
            assert settings.connect_timeout == 1.5
            assert settings.read_timeout == 2.5

    async def test_signs_with_signature_version_four(self) -> None:
        """An older version is refused by every bucket in a region created after 2014.

        Asserted through a signed URL rather than by reading the client's configuration back:
        the algorithm in the query string is the thing S3 actually checks, and it is what the
        configuration exists to produce.
        """
        url = await S3Client(AwsSession(AwsConfig(region=REGION))).presigned_get_url(
            "bucket", "key"
        )
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url

    def test_needs_no_credentials_to_construct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What lets an application compose in CI, with no AWS account, to print its schema.

        Credentials resolve to nothing here and the failure arrives at the first call that has
        to sign — which is the behaviour `dexter.aws`'s module docstring promises, so it is
        worth a test rather than a comment.
        """
        monkeypatch.delenv("AWS_ACCESS_KEY_ID")
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
        monkeypatch.delenv("AWS_SESSION_TOKEN")

        assert AwsSession(AwsConfig(region=REGION)).s3 is not None

    def test_constructing_reads_nothing_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The property this class exists to have**, and the reason it is lazy.

        Building a client reads the machine's shared AWS configuration, and a default profile
        boto3 cannot use fails right there. Eagerly, that failure reaches an application that
        was never going to call AWS — an unconfigured deployment asking for storage it has not
        been given would be told about a credential provider rather than about its own missing
        configuration. It was found exactly that way: a machine whose `[default]` profile used
        `login_session` answered a completely unrelated request with a botocore dependency error.
        """
        monkeypatch.setenv("AWS_PROFILE", "definitely-not-a-profile")

        assert AwsSession(AwsConfig(region=REGION)).config.region == REGION

    def test_an_unreadable_configuration_does_not_leak_a_botocore_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure arrives at first use, and it arrives translated.

        Naming a profile that does not exist raises `ProfileNotFound` while the chain is being
        resolved — before any request, so nothing in `_calling.py` would see it. Without the
        guard in `session.py` this is the single way a consumer ends up importing
        `botocore.exceptions` to handle a dexter failure.
        """
        monkeypatch.setenv("AWS_PROFILE", "definitely-not-a-profile")
        session = AwsSession(AwsConfig(region=REGION))

        with pytest.raises(CredentialsUnavailableError, match="could not be read"):
            _ = session.s3

    def test_each_client_is_built_once(self) -> None:
        """Cached, because parsing a service model per call would be paid on every request."""
        session = AwsSession(AwsConfig(region=REGION))

        assert session.s3 is session.s3
        assert session.secrets is session.secrets


class TestWiring:
    @pytest.mark.parametrize(
        "client",
        [
            S3Client,
            SecretsManagerClient,
            ParameterStoreClient,
            SesClient,
            SnsClient,
            SqsClient,
        ],
    )
    async def test_binds_every_client(self, client: type[Any]) -> None:
        container = wire()
        try:
            assert isinstance(await container.resolve(client), client)
        finally:
            await container.aclose()

    async def test_the_session_is_shared(self) -> None:
        """Two sessions would mean two connection pools, each half as useful."""
        container = wire()
        try:
            assert await container.resolve(AwsSession) is await container.resolve(
                AwsSession
            )
        finally:
            await container.aclose()

    async def test_the_secrets_client_is_shared(self) -> None:
        """It holds the cache. A fresh one per caller is a cache that never hits."""
        container = wire()
        try:
            first = await container.resolve(SecretsManagerClient)
            assert first is await container.resolve(SecretsManagerClient)
        finally:
            await container.aclose()

    async def test_a_missing_config_names_the_registration_to_add(self) -> None:
        container = wire(with_config=False)
        try:
            with pytest.raises(UnregisteredDependencyError, match="AwsConfig"):
                await container.resolve(S3Client)
        finally:
            await container.aclose()


class TestValueWiring:
    """The provider pattern: a marker in, a `ValueSource` out, and nothing in between."""

    async def test_a_secret_value_resolves_under_the_application_s_marker(self) -> None:
        container = wire_value(
            lambda builder: register_secret_value(
                builder,
                DatabasePassword,
                secret_id=SECRET_ID,
                secret_key="DATABASE_PASSWORD",
                scope=Scope.SINGLETON,
            )
        )
        try:
            password = await container.resolve(DatabasePassword)
            assert isinstance(password, SecretValue)
            assert repr(password) == f"SecretValue('{SECRET_ID}', 'DATABASE_PASSWORD')"
        finally:
            await container.aclose()

    async def test_a_parameter_value_resolves_under_its_own_marker(self) -> None:
        container = wire_value(
            lambda builder: register_parameter_value(
                builder, OrdersTableName, name="/app/orders", scope=Scope.SINGLETON
            )
        )
        try:
            table = await container.resolve(OrdersTableName)
            assert isinstance(table, ParameterValue)
            assert repr(table) == "ParameterValue('/app/orders')"
        finally:
            await container.aclose()

    async def test_a_static_value_needs_no_aws_at_all(self) -> None:
        """The substitution the whole pattern exists for: same marker, no account, no network."""
        builder = ContainerBuilder()
        builder.register(DatabasePassword).to_instance(StaticValue("hunter2"))

        container = builder.build()
        try:
            password = await container.resolve(DatabasePassword)
            assert await password.value() == "hunter2"
        finally:
            await container.aclose()

    def test_a_secret_value_before_use_aws_names_the_call_to_add(self) -> None:
        """Named here rather than left to resolution, which would name a class nobody wrote."""
        builder = ContainerBuilder()

        with pytest.raises(AwsWiringError, match="use_aws"):
            register_secret_value(
                builder,
                DatabasePassword,
                secret_id=SECRET_ID,
                secret_key="DATABASE_PASSWORD",
                scope=Scope.SINGLETON,
            )

    def test_a_parameter_value_before_use_aws_names_the_call_to_add(self) -> None:
        builder = ContainerBuilder()

        with pytest.raises(AwsWiringError, match="use_aws"):
            register_parameter_value(
                builder, OrdersTableName, name="/app/orders", scope=Scope.SINGLETON
            )

    def test_the_wiring_error_names_the_marker_that_could_not_be_bound(self) -> None:
        builder = ContainerBuilder()

        with pytest.raises(AwsWiringError, match="OrdersTableName"):
            register_parameter_value(
                builder, OrdersTableName, name="/app/orders", scope=Scope.SINGLETON
            )

    async def test_two_markers_over_one_client_stay_distinct(self) -> None:
        """Structurally identical protocols, told apart by identity.

        mypy would let one be passed where the other is expected, because they have the same
        shape. It does not matter: nobody passes these by hand — the container does, and the
        container keys on the class itself.
        """

        def register(builder: ContainerBuilder) -> None:
            register_parameter_value(
                builder, OrdersTableName, name="/app/orders", scope=Scope.SINGLETON
            )
            register_parameter_value(
                builder, DatabasePassword, name="/app/password", scope=Scope.SINGLETON
            )

        container = wire_value(register)
        try:
            table = await container.resolve(OrdersTableName)
            password = await container.resolve(DatabasePassword)
            assert repr(table) == "ParameterValue('/app/orders')"
            assert repr(password) == "ParameterValue('/app/password')"
        finally:
            await container.aclose()

    async def test_an_unregistered_marker_names_itself(self) -> None:
        container = wire()
        try:
            with pytest.raises(UnregisteredDependencyError, match="OrdersTableName"):
                await container.resolve(OrdersTableName)
        finally:
            await container.aclose()

    async def test_a_scoped_value_is_refused_for_a_singleton_consumer(self) -> None:
        """`Scope.SCOPED` here is the mistake the docstring warns about, and the container
        catches it at `build()` rather than at the first request."""

        class Consumer:
            def __init__(self, password: DatabasePassword) -> None:
                self.password = password

        builder = ContainerBuilder()
        use_aws(builder)
        register_aws_config(builder, AwsConfig(region=REGION))
        register_secret_value(
            builder,
            DatabasePassword,
            secret_id=SECRET_ID,
            secret_key="DATABASE_PASSWORD",
            scope=Scope.SCOPED,
        )
        builder.register(Consumer).to(Consumer, scope=Scope.SINGLETON)

        with pytest.raises(CaptiveDependencyError):
            builder.build()

    async def test_wiring_a_value_reaches_no_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**What lets `build_container()` run in CI with no AWS account.**

        Registering and resolving a value must construct a pointer and nothing else. Asserted by
        removing every credential: a fetch during wiring or resolution would have to sign, and
        signing with nothing raises. Only `await value()` is allowed to reach AWS.
        """
        monkeypatch.delenv("AWS_ACCESS_KEY_ID")
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
        monkeypatch.delenv("AWS_SESSION_TOKEN")

        container = wire_value(
            lambda builder: register_secret_value(
                builder,
                DatabasePassword,
                secret_id=SECRET_ID,
                secret_key="DATABASE_PASSWORD",
                scope=Scope.SINGLETON,
            )
        )
        try:
            assert isinstance(await container.resolve(DatabasePassword), SecretValue)
        finally:
            await container.aclose()
