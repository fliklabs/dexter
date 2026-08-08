"""Shared fixtures for the AWS module's tests.

**`botocore.stub.Stubber` against a real client, rather than a hand-written fake.** The
difference is not stylistic: the stubber validates every request this module makes against
botocore's own service model, so a misspelled parameter or a wrong type fails here rather than
in production. A fake would happily accept `Buckett=` and prove nothing.

**Credentials are faked by an autouse fixture, and that is a safety property rather than a
convenience.** Without it boto3 would walk its real chain — a developer's `~/.aws/credentials`,
then the metadata endpoint — which means a test suite that reaches a real account when a stub is
missing, and one that hangs for the IMDS timeout on a machine with no credentials at all. Both
are silent. Fixed fake values make every signature deterministic and every miss immediate.
"""

import os
from collections.abc import Iterator

import pytest
from botocore.stub import Stubber

from dexter.aws import AwsConfig, AwsSession

REGION = "ap-southeast-2"
BUCKET = "plum-test-bucket"
SECRET_ID = "plum/test/secrets"


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give boto3 an identity that is definitely not anybody's, and no config to read.

    Three of these five are not decoration, and each was earned by a real failure:

    - `AWS_EC2_METADATA_DISABLED`, or a machine with nothing configured spends the metadata
      timeout on every client that has to sign.
    - `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` pointed at `/dev/null`, or the
      developer's own `~/.aws/config` takes part in the test. That is not hypothetical: a
      `login_session` profile on this machine made client construction raise
      `MissingDependencyException` the first time a test removed the environment keys — a
      failure that would never reproduce in CI, and never on a colleague's laptop.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    )
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test-session-token")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def config() -> AwsConfig:
    """Configuration with a short cache, so a TTL test does not have to wait five minutes."""
    return AwsConfig(region=REGION, secret_cache_seconds=60.0)


@pytest.fixture
def session(config: AwsConfig) -> AwsSession:
    """A real session, with real boto3 clients that no request will leave."""
    return AwsSession(config)


@pytest.fixture
def s3_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the S3 client, and fail the test if a queued response went unused.

    The unused-response check is the half that catches the more interesting mistake: a test
    that queues two responses and makes one call is asserting something it thinks happened
    twice.
    """
    with Stubber(session.s3) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def secrets_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the Secrets Manager client, with the same unused-response check."""
    with Stubber(session.secrets) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def dynamodb_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the DynamoDB client."""
    with Stubber(session.dynamodb) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def ses_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the SES v2 client."""
    with Stubber(session.ses) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def sns_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the SNS client."""
    with Stubber(session.sns) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def sqs_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the SQS client."""
    with Stubber(session.sqs) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def ssm_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the Systems Manager client."""
    with Stubber(session.ssm) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()
