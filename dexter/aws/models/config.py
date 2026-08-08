"""How to reach AWS: where, how hard to lean on it, and how long to remember what it said.

Frozen pydantic, because this crosses from a consumer's own code into dexter and is built once —
exactly the split AGENTS.md draws. Every field is hashable, so the frozen model really is.

**Nothing here mentions credentials, and nothing can.** boto3 resolves an identity from the
environment, a profile, a `credential_process`, a container endpoint or instance metadata, and
refreshes a short-lived one before it expires. Adding a key to this model would be dexter
offering a worse version of something the SDK already does properly — and would put a secret in
a model that gets printed. A process outside AWS is served by pointing
`AWS_EC2_METADATA_SERVICE_ENDPOINT` at something speaking IMDSv2, with nothing asked of this
module. See `dexter/aws/session.py`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class RetryMode(StrEnum):
    """Which retry policy botocore applies underneath every call."""

    LEGACY = "LEGACY"
    """botocore's historical default. Retries a narrower set of failures and has no
    client-side rate limiting."""

    STANDARD = "STANDARD"
    """The documented modern policy: a wider, consistent set of retryable failures, shared
    across the AWS SDKs, with a token bucket that stops a throttled client from making a
    throttle worse."""

    ADAPTIVE = "ADAPTIVE"
    """`STANDARD` plus client-side rate limiting that backs the whole client off when the
    service throttles. Experimental in botocore's own documentation, and it slows every caller
    in the process — worth choosing deliberately, never by default."""


def describe_retry_mode(mode: RetryMode, /) -> str:
    """Render a retry mode as the symbol a caller would type.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence. In a message aimed at
    a developer the qualified symbol is more useful: it is what they have to write.
    """
    return f"RetryMode.{mode.name}"


class AwsEndpoints(BaseModel):
    """Where to send requests instead of the real services.

    **Per service rather than one URL, because the common local setup is not one server.**
    LocalStack does answer for everything on one port, and `default` is for exactly that. But
    the more usual arrangement is DynamoDB Local on `:8000` beside MinIO on `:9000`, and a
    single `endpoint_url` cannot say that — it would point Secrets Manager at the object store.

    A service's own field wins over `default`; `default` wins over the real AWS endpoint. All
    unset means ordinary AWS, which is what a deployment wants and why every field is optional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: str | None = None
    """Where every service goes unless it names its own endpoint below."""

    s3: str | None = None
    dynamodb: str | None = None
    secrets: str | None = None
    ses: str | None = None
    sns: str | None = None
    sqs: str | None = None
    ssm: str | None = None


class AwsConfig(BaseModel):
    """Where AWS is, how hard to lean on it, and how long answers stay good.

    Frozen and `extra="forbid"` like every dexter type built once from what a consumer wrote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str
    """The region every client is built for, such as `ap-southeast-2`.

    Required rather than left to boto3's own resolution, because the fallback chain ends in a
    client that raises `NoRegionError` at first use — a long way from the wiring that forgot it.
    """

    endpoints: AwsEndpoints = AwsEndpoints()
    """Local or recorded stand-ins to use instead of the real services."""

    max_pool_connections: int = 50
    """How many concurrent connections one client may hold.

    botocore's default is 10, which is low for anything fanning out over objects: the eleventh
    concurrent request does not fail, it silently waits, and the symptom is latency rather than
    an error.

    **This is headroom, not the real limit.** Every call in this module runs on
    `asyncio.to_thread`, whose default executor caps in-flight work at `min(32, cpu_count + 4)`
    — so that number, not this one, is what binds first. See `dexter/aws/AGENTS.md`.
    """

    connect_timeout_seconds: float = 5.0
    """How long to wait for a connection before giving up.

    botocore's default is 60, which for a request-path call is a minute spent failing. It also
    holds a worker thread for the whole minute, and there are about thirty of those.
    """

    read_timeout_seconds: float = 30.0
    """How long to wait for a response before giving up.

    **Must exceed the longest SQS long poll**, which defaults to 20 seconds — a read timeout
    below it would abort every empty receive as a failure. That coupling is the only reason
    this is not smaller.
    """

    max_attempts: int = 3
    """How many times botocore may try one call in total, **including the first**.

    Three means one try and two retries. It is deliberately not botocore's own `max_attempts`,
    which counts only the retries and so means one more than it looks like — see
    `dexter/aws/session.py`, which maps this onto `total_max_attempts` for exactly that reason.

    Lower than botocore's default of five, because every attempt occupies one of the ~32
    executor threads for its whole duration, and DynamoDB's batch loop adds its own retries on
    top of these.
    """

    retry_mode: RetryMode = RetryMode.STANDARD
    """Which retry policy to apply. See `RetryMode`; botocore's own default is `LEGACY`."""

    secret_cache_seconds: float = 300.0
    """How long a fetched secret stays good.

    Not zero, and that is the whole point of it being here. A value resolved from Secrets
    Manager is read on *every* call that needs it, so an uncached one turns each request into an
    extra network round-trip and a per-request charge. Five minutes is short enough that a
    rotated secret takes effect without a deploy, and long enough that the fetch stops showing
    up in a latency profile — and AWS's own rotation keeps the previous version valid well past
    it.
    """

    parameter_cache_seconds: float = 60.0
    """How long a fetched SSM parameter stays good.

    Shorter than `secret_cache_seconds` deliberately, because the two are changed for different
    reasons. A rotated credential is a scheduled event with an overlap window; a repointed table
    name or a flipped flag is an operator at a console who expects it to take effect *now*.
    Standard-tier `GetParameter` is also free, so the shorter lifetime costs nothing.
    """

    @field_validator("region")
    @classmethod
    def _check_region(cls, region: str) -> str:
        """Reject a blank region here rather than as a `NoRegionError` at first use."""
        if not region.strip():
            raise ValueError("region must name an AWS region.")
        return region
