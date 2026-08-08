"""Running one boto3 call off the event loop, and translating what it raises.

Two things every client in this module needs, in one place so that neither can drift.

**`asyncio.to_thread`, and the trade it represents.** dexter is async-native and AGENTS.md says
so; boto3 is synchronous and there is no supported asynchronous build of it. Wrapping each call
in a thread keeps the public surface honest — every method a consumer touches is `async def`,
and nothing blocks the loop — at the cost of a thread hop per request. That cost is real and
small: a thread from the default executor against a call that is about to spend milliseconds on
a network. The alternative considered and rejected was an unofficial async AWS client, which
would trade a known cost for an unknown maintenance one.

**The default executor is the real concurrency limit, and it is not large.** `asyncio.to_thread`
runs on the loop's default `ThreadPoolExecutor`, which sizes itself to `min(32, cpu_count + 4)`
— so about thirty-two calls can be in flight at once regardless of what
`AwsConfig.max_pool_connections` says. Anything that holds a thread without using it spends
that budget: one SQS receive with a twenty-second long poll occupies a slot for twenty seconds.
dexter drives no event loop and so does not set the executor; an application that fans out
widely should. See `dexter/aws/AGENTS.md`.

**Translation happens here or not at all.** `ClientError` is a single class covering "no such
key", "access denied" and "slow down", separated only by a string nested two levels into a
response dictionary. Letting it out would make every caller parse that dictionary to decide
whether to retry, so the boundary is this file.
"""

import asyncio
from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from .errors import (
    AccessDeniedError,
    AwsError,
    AwsRequestError,
    CredentialsUnavailableError,
    ResourceNotFoundError,
    ThrottledError,
)

NO_CREDENTIALS_CODES = frozenset(
    {"InvalidClientTokenId", "UnrecognizedClientException", "ExpiredToken"}
)
"""Service-side answers that mean the identity is the problem, not the request.

`NoCredentialsError` covers the case where boto3 found nothing to sign with. These are the other
half: it found something and the service refused it — a stale session, or a key from a different
account. Both point at the credential helper rather than at the calling code, so both become
`CredentialsUnavailableError`.
"""

ERROR_CODES: dict[str, type[AwsError]] = {
    # Throttling. Seven spellings across seven services for one condition, which is exactly the
    # kind of thing a caller should never have to know.
    "Throttling": ThrottledError,
    "ThrottlingException": ThrottledError,
    "ThrottledException": ThrottledError,
    "RequestThrottled": ThrottledError,
    "RequestThrottledException": ThrottledError,
    "TooManyRequestsException": ThrottledError,
    "RequestLimitExceeded": ThrottledError,
    "ProvisionedThroughputExceededException": ThrottledError,
    "SlowDown": ThrottledError,
    # Denied by policy, as opposed to unidentified. The KMS one arrives when a SecureString
    # parameter or an encrypted secret is readable but its key is not.
    "AccessDenied": AccessDeniedError,
    "AccessDeniedException": AccessDeniedError,
    "UnauthorizedOperation": AccessDeniedError,
    "AuthorizationError": AccessDeniedError,
    "KMSAccessDeniedException": AccessDeniedError,
    # The container is missing, not its contents.
    "NoSuchBucket": ResourceNotFoundError,
    "ResourceNotFoundException": ResourceNotFoundError,
    "QueueDoesNotExist": ResourceNotFoundError,
    "AWS.SimpleQueueService.NonExistentQueue": ResourceNotFoundError,
}
"""Which exception a service's short code becomes.

A table rather than a chain of `if`s, so adding a service adds rows and changes no logic — and
so there is no final unreachable branch to leave uncovered. Anything absent falls through to
`AwsRequestError`, which is the honest answer for a code nobody has classified.

**Only codes that mean the same thing everywhere belong here.** A code whose meaning depends on
which service said it — DynamoDB's `ConditionalCheckFailedException`, S3's `404` — is handled by
the client that knows the context, inside `work`, before this table is consulted.
"""


def error_code(error: ClientError) -> str:
    """The service's short code for a failure, or an empty string if it named none.

    Reaches into the response dictionary in exactly one place, so that no caller has to know
    the shape of it.
    """
    return str(error.response.get("Error", {}).get("Code", ""))


async def call[T](operation: str, work: Callable[[], T]) -> T:
    """Run `work` in a worker thread and translate any AWS failure it raises.

    Args:
        operation: What was being attempted, named the way a reader would recognise it —
            `"PutObject s3://bucket/key"` rather than `"put"`. It is the whole of what the
            raised message can say, so it should say enough.
        work: A no-argument callable making one boto3 call. Anything it catches itself — a
            404 a caller wants as `None`, say — never reaches the translation below, and
            anything it raises that is already an `AwsError` passes through untouched.

    Returns:
        Whatever `work` returned.

    Raises:
        CredentialsUnavailableError: If no identity could be found, or the one found was
            refused.
        ThrottledError: If the service asked this process to slow down.
        AccessDeniedError: If the identity is valid but the policy forbids the call.
        ResourceNotFoundError: If the bucket, table, queue or topic does not exist.
        AwsRequestError: For anything else the service said, and for a transport failure.
    """
    try:
        return await asyncio.to_thread(work)
    except NoCredentialsError as error:
        raise CredentialsUnavailableError(
            f"{operation} found no AWS credentials: {error}"
        ) from error
    except ClientError as error:
        code = error_code(error)
        if code in NO_CREDENTIALS_CODES:
            raise CredentialsUnavailableError(
                f"{operation} was refused because the AWS credentials were rejected ({code})."
            ) from error
        failure = ERROR_CODES.get(code, AwsRequestError)
        raise failure(f"{operation} was refused with {code}: {error}") from error
    except BotoCoreError as error:
        # Everything botocore raises that is not a service answer: a timeout, a DNS failure,
        # an unresolvable endpoint. **`ClientError` is not caught by this** — despite the name
        # it does not inherit from `BotoCoreError`, the two are siblings under `Exception`.
        # That is why it has its own clause above rather than being narrowed out of this one,
        # and why removing either clause silently stops translating half the failures.
        raise AwsRequestError(f"{operation} could not be completed: {error}") from error
