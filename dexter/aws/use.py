"""Wiring: how the AWS clients, and the values behind them, are bound into a container.

The same shapes every dexter module uses. `use_aws(builder)` says this application talks to AWS
and takes no configuration; the `register_*` functions are what the *application* contributes,
because a region and a secret's name are values an application owns and dexter reads no
environment::

    use_aws(builder)
    register_aws_config(builder, AwsConfig(region="ap-southeast-2"))
    register_secret_value(
        builder,
        DatabasePassword,
        secret_id="app/production/secrets",
        secret_key="DATABASE_PASSWORD",
        scope=Scope.SINGLETON,
    )

**`use_aws` must run before either value helper**, because they resolve against clients it binds.
The wrong order raises `AwsWiringError` naming the missing call, rather than letting the mistake
surface at resolve time as the container reporting a class the application never mentioned.
`register_aws_config` is order-independent: nothing reads it while wiring.

**There is no second `use_*` here, and so no test double.** Everywhere else in dexter an engine
has a recording counterpart, because the alternative is a test suite that sends real mail or
scrapes a real site. AWS is different in one way that matters: `AwsConfig.endpoints` points the
real clients at a different server, so a test gets a genuine `S3Client` talking to a local
stand-in, exercising the actual boto3 request path — and `botocore.stub.Stubber` validates every
request against botocore's own service model. A hand-written fake would be easier to write and
would prove less.

The one substitution that *is* worth making is a value's, and it needs nothing from this module::

    builder.register(DatabasePassword).to_instance(StaticValue("hunter2"))
"""

from dexter.commons import describe_type
from dexter.dependency_injection import ContainerBuilder, Key, Scope

from .dynamodb import DynamoDbClient
from .errors import AwsWiringError
from .models import AwsConfig
from .parameters import ParameterStoreClient, ParameterValue
from .s3 import S3Client
from .secrets import SecretsManagerClient, SecretValue
from .ses import SesClient
from .session import AwsSession
from .sns import SnsClient
from .sqs import SqsClient
from .values import ValueSource


def use_aws(builder: ContainerBuilder) -> None:
    """Bind the AWS session and every client over it.

    All are `Scope.SINGLETON`. `AwsSession` must be, because it holds the boto3 clients and their
    connection pools, and building a second set would double the pools while halving the reuse.
    The clients over it must be for a subtler reason: `SecretsManagerClient` and
    `ParameterStoreClient` hold the caches, and a transient one would hand every caller an empty
    cache — which fails in the most expensive way available, by working perfectly and fetching
    every time.

    A client that is never resolved costs nothing: `AwsSession` builds each boto3 client on
    first use, so binding all seven does not mean constructing all seven.
    """
    builder.register(AwsSession).to(AwsSession, scope=Scope.SINGLETON)
    builder.register(S3Client).to(S3Client, scope=Scope.SINGLETON)
    builder.register(DynamoDbClient).to(DynamoDbClient, scope=Scope.SINGLETON)
    builder.register(SecretsManagerClient).to(
        SecretsManagerClient, scope=Scope.SINGLETON
    )
    builder.register(ParameterStoreClient).to(
        ParameterStoreClient, scope=Scope.SINGLETON
    )
    builder.register(SesClient).to(SesClient, scope=Scope.SINGLETON)
    builder.register(SnsClient).to(SnsClient, scope=Scope.SINGLETON)
    builder.register(SqsClient).to(SqsClient, scope=Scope.SINGLETON)


def register_aws_config(builder: ContainerBuilder, config: AwsConfig, /) -> None:
    """Bind the region, endpoints, timeouts and cache lifetimes the clients are built with.

    Nothing is constructed here, so there is no `scope=` to choose: an existing object is
    inherently a single object.
    """
    builder.register(AwsConfig).to_instance(config)


def register_secret_value(
    builder: ContainerBuilder,
    key: Key[ValueSource],
    /,
    *,
    secret_id: str,
    secret_key: str,
    scope: Scope,
) -> None:
    """Bind `key` to one named value inside one JSON secret.

    Args:
        builder: The container being wired.
        key: The application's marker for this value — a one-line `Protocol` subclass of
            `ValueSource`. It is what a component annotates, and what the container names if the
            binding is missing.
        secret_id: The secret's name, such as `app/production/secrets`.
        secret_key: The name inside it, such as `DATABASE_PASSWORD`.
        scope: Almost always `Scope.SINGLETON`. The object is a pointer at a location and holds
            no value, so there is nothing per-request about it — and the fetch it delegates to is
            cached on the client, which is a singleton regardless. `Scope.SCOPED` would make
            every singleton that depends on this value fail `build()` with
            `CaptiveDependencyError`, which is the container being right. It is required rather
            than defaulted for the same reason `Binder.to` requires it: a lifetime chosen by
            omission is a lifetime nobody chose.

    Raises:
        AwsWiringError: If `use_aws(builder)` has not run.
    """
    _require_wiring(builder, key, SecretsManagerClient)

    def provide(client: SecretsManagerClient) -> SecretValue:
        return SecretValue(client, secret_id, secret_key)

    builder.register(key).to(provide, scope=scope)


def register_parameter_value(
    builder: ContainerBuilder,
    key: Key[ValueSource],
    /,
    *,
    name: str,
    decrypt: bool = True,
    scope: Scope,
) -> None:
    """Bind `key` to one parameter in the parameter store.

    Args:
        builder: The container being wired.
        key: The application's marker for this value. See `register_secret_value`.
        name: The parameter's full name, such as `/app/production/orders-table`.
        decrypt: Whether to ask the service to decrypt a `SecureString`. Left on, because the
            alternative is a parameter whose value arrives as ciphertext that looks like an
            ordinary string and fails somewhere far away.
        scope: See `register_secret_value`. `Scope.SINGLETON` is the answer.

    Raises:
        AwsWiringError: If `use_aws(builder)` has not run.
    """
    _require_wiring(builder, key, ParameterStoreClient)

    def provide(client: ParameterStoreClient) -> ParameterValue:
        return ParameterValue(client, name, decrypt=decrypt)

    builder.register(key).to(provide, scope=scope)


def _require_wiring(
    builder: ContainerBuilder, key: Key[ValueSource], client: type[object]
) -> None:
    """Refuse a value binding whose client has not been registered.

    Checked here rather than left to resolution, because AGENTS.md is explicit that the wrong
    order must raise a module error naming the missing call. Without this the failure arrives
    much later as `UnregisteredDependencyError` naming a class the application never wrote down.
    """
    if not builder.is_registered(client):
        raise AwsWiringError(
            f"{describe_type(key)} cannot be bound to an AWS value before "
            f"{describe_type(client)} is registered. Call `use_aws(builder)` first."
        )
