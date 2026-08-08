"""The one place boto3 is constructed.

Everything else in this module reaches AWS through the seven clients built here, so there is
exactly one file to read to know how this process authenticates, where it sends requests, how
long it waits, and how many times it tries.

**dexter contributes no credential code, and that is most of what choosing boto3 bought.**
The SDK resolves an identity from environment variables, a shared profile, a
`credential_process`, a container endpoint or instance metadata, and refreshes a short-lived one
before it expires. A host outside AWS is served by pointing `AWS_EC2_METADATA_SERVICE_ENDPOINT`
at something that speaks IMDSv2 — IAM Roles Anywhere's `aws_signing_helper serve` does exactly
that — and no line of this module is involved. Reimplementing signature version 4 and a
credential chain would have been a great deal of code whose only possible outcome was parity.

**Each client is built once, on first use, and held for the life of the process.**
Constructing one costs about a tenth of a second — it parses the service's JSON model — so it is
worth caching; and it *reads the machine's shared AWS configuration*, so it is worth deferring.
Seven eager clients would be most of a second of startup for an application that uses two.

That second half is the reason this is lazy rather than eager. Resolving the credential chain
reads `~/.aws/config`, and a default profile boto3 cannot use — an auth method whose optional
dependency is not installed, a profile that does not exist — fails right there. Built eagerly,
that failure reaches an application that was never going to call AWS at all: an unconfigured
deployment asking for storage it has not been given would be told about a credential provider
instead of about its own missing configuration. Deferred, the failure arrives at the first call
that actually needs AWS, which is where it is both true and actionable.

Having no credentials at all is a different thing again, and is not a failure here: the chain
resolves to nothing, and `NoCredentialsError` comes from the first call that has to sign. That
is what lets an application compose in CI, with no AWS account, to print its OpenAPI document.

**Reading the configuration can nonetheless fail, and it is guarded.** That is a botocore
exception on a code path outside `_calling.py`, so without the guard below it would be the one
failure in this module that reaches a consumer untranslated.

**Each client is constructed where it is returned, rather than through a shared helper.**
`boto3-stubs` overloads `Session.client` on the *literal* service name, so `client("s3")` is an
`S3Client` and `client(service)` for a variable `service` is `Any` — routing seven services
through one function would quietly discard every bit of checking the stubs exist to provide. The
endpoint lookups are spelled out seven times for the same reason: `getattr(endpoints, service)`
is the same mistake wearing a different hat. Only the error translation is shared, as a context
manager, because that part does not care which service it is wrapping.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import TYPE_CHECKING, Literal, TypedDict

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import BotoCoreError

from .errors import CredentialsUnavailableError
from .models import AwsConfig, RetryMode

RETRY_MODES: dict[RetryMode, Literal["legacy", "standard", "adaptive"]] = {
    RetryMode.LEGACY: "legacy",
    RetryMode.STANDARD: "standard",
    RetryMode.ADAPTIVE: "adaptive",
}
"""How dexter's spelling of a retry mode maps onto botocore's.

A table rather than `mode.value.lower()`, and the difference is not style. botocore types this
field as a literal of three exact strings, so a value computed at runtime is a `str` and fails to
check — the conversion has to be one mypy can follow. Writing it out also means a member added to
`RetryMode` without a mapping is a type error here rather than a service error much later.
"""


class Retries(TypedDict, total=False):
    """The retry policy botocore takes, in the shape it takes it.

    Declared here because the stubs' own version of this is private, and importing a private
    name that exists only in a stub would mean a second `TYPE_CHECKING` block for one alias.
    TypedDict compatibility is structural, so a matching declaration is accepted where theirs is
    expected — including `total=False`, which has to match: a TypedDict whose keys are *required*
    is not assignable to one whose keys are optional.
    """

    mode: Literal["legacy", "standard", "adaptive"]
    total_max_attempts: int
    max_attempts: int
    """Declared but never set, and deliberately not the one this module uses.

    **botocore's `max_attempts` counts retries *after* the first try, not attempts.** It
    normalises `{"max_attempts": 3}` into `{"total_max_attempts": 4}`, so configuring it with
    the number a reader would call "how many attempts" quietly buys one more than they asked
    for. `total_max_attempts` means what it says, so `AwsConfig.max_attempts` maps onto that
    instead and the two spellings agree.

    It stays declared because structural compatibility between TypedDicts is by key set rather
    than by the subset actually used, and leaving it out makes the whole thing unassignable to
    botocore's.
    """


if TYPE_CHECKING:
    # **The one `TYPE_CHECKING` import in dexter, and it does not break the rule that bans
    # them.** AGENTS.md disables ruff's `TC` family because *constructor* annotations are read
    # at runtime by the container, so a class named in one must stay importable. These are
    # return annotations on seven properties: nothing introspects them, and PEP 649 makes
    # annotations lazy on 3.14 anyway, so the names are never looked up.
    #
    # They have to be guarded, because `boto3-stubs` is in the `lint` dependency group — a
    # consumer's install does not contain it, and importing it at runtime would break every
    # application that uses this module. Without the annotations the clients would be `Any`,
    # which would throw away exactly the checking the stubs are here to provide.
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_secretsmanager.client import SecretsManagerClient
    from mypy_boto3_sesv2.client import SESV2Client
    from mypy_boto3_sns.client import SNSClient
    from mypy_boto3_sqs.client import SQSClient
    from mypy_boto3_ssm.client import SSMClient


class AwsSession:
    """The boto3 clients this process uses, each built once, on first use.

    `Scope.SINGLETON`. A boto3 client is thread-safe and holds a connection pool, so sharing one
    is the intended use; building a second would double the pool and halve its usefulness.

    **No `__slots__`, and that is what `cached_property` costs.** One instance exists per
    process, so the dictionary is a few hundred bytes against seven connection pools — and
    the alternative is annotating the cached clients by hand, which would mean importing the
    stub-only types that exist in no consumer's install.
    """

    def __init__(self, config: AwsConfig) -> None:
        """Record how to reach AWS, constructing nothing and reading nothing.

        Args:
            config: Which region to talk to, where, how long to wait, and how often to retry.
        """
        self.config = config

    @cached_property
    def s3(self) -> S3Client:
        """The S3 client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            # `endpoint_url=None` is boto3's own "use the real service" value, so an unset
            # override needs no branch here.
            return self._session().client(
                "s3",
                endpoint_url=self.config.endpoints.s3 or self.config.endpoints.default,
                config=self._s3_botocore_config(),
            )

    @cached_property
    def dynamodb(self) -> DynamoDBClient:
        """The DynamoDB client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "dynamodb",
                endpoint_url=self.config.endpoints.dynamodb
                or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    @cached_property
    def secrets(self) -> SecretsManagerClient:
        """The Secrets Manager client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "secretsmanager",
                endpoint_url=self.config.endpoints.secrets
                or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    @cached_property
    def ses(self) -> SESV2Client:
        """The SES v2 client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "sesv2",
                endpoint_url=self.config.endpoints.ses or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    @cached_property
    def sns(self) -> SNSClient:
        """The SNS client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "sns",
                endpoint_url=self.config.endpoints.sns or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    @cached_property
    def sqs(self) -> SQSClient:
        """The SQS client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "sqs",
                endpoint_url=self.config.endpoints.sqs or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    @cached_property
    def ssm(self) -> SSMClient:
        """The Systems Manager client, built on first use and kept.

        Raises:
            CredentialsUnavailableError: If the ambient AWS configuration could not be read.
        """
        with self._readable():
            return self._session().client(
                "ssm",
                endpoint_url=self.config.endpoints.ssm or self.config.endpoints.default,
                config=self._botocore_config(),
            )

    def _session(self) -> boto3.session.Session:
        """A boto3 session for the configured region.

        One per client rather than one shared: a session is a thin holder for the credential
        resolver, and the clients it produces do not reference it afterwards.
        """
        return boto3.session.Session(region_name=self.config.region)

    def _botocore_config(self) -> BotocoreConfig:
        """How every client behaves, whichever service it speaks to."""
        return BotocoreConfig(
            max_pool_connections=self.config.max_pool_connections,
            connect_timeout=self.config.connect_timeout_seconds,
            read_timeout=self.config.read_timeout_seconds,
            retries=self._retries(),
        )

    def _retries(self) -> Retries:
        """The retry policy every client is built with."""
        return Retries(
            mode=RETRY_MODES[self.config.retry_mode],
            total_max_attempts=self.config.max_attempts,
        )

    def _s3_botocore_config(self) -> BotocoreConfig:
        """The base configuration, plus the signature version S3 needs.

        **`signature_version` belongs to S3 alone and must never reach another client.**
        `s3v4` names `botocore.auth.S3SigV4Auth`, and botocore applies whatever this field says
        to whichever service is being built — it does not check that the two agree. That class
        skips URL-path normalisation and injects `X-Amz-Content-SHA256` from a payload-signing
        decision only S3 makes, so handing it to DynamoDB or SQS signs their requests with S3's
        rules. It may verify anyway, because the header is in `SignedHeaders` and the round trip
        is self-consistent — which is what makes the mistake survivable, and therefore durable,
        until a service needs `v4-unsigned-body` or bearer auth and answers a 400 naming nothing.

        S3 does need it: a presigned URL signed with an older version is rejected by every bucket
        in a region created after 2014.
        """
        return BotocoreConfig(
            max_pool_connections=self.config.max_pool_connections,
            connect_timeout=self.config.connect_timeout_seconds,
            read_timeout=self.config.read_timeout_seconds,
            retries=self._retries(),
            signature_version="s3v4",
        )

    @contextmanager
    def _readable(self) -> Iterator[None]:
        """Translate a failure to read the AWS configuration.

        Shared because it does not care which service is being built, where the construction
        itself cannot be — see the note in the module docstring about the literal overload.
        """
        try:
            yield
        except BotoCoreError as error:
            raise CredentialsUnavailableError(
                f"The AWS configuration could not be read: {error}"
            ) from error
