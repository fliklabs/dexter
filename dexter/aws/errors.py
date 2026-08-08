"""Exceptions raised by the AWS module.

The same convention as the rest of dexter: `args` stays a short one-line message, so
`pytest.raises(match=...)` and log lines remain readable.

**Every botocore failure is translated, and none escapes.** `ClientError` is one exception class
covering "no such bucket", "access denied" and "you are being throttled", distinguished only by a
string buried two levels into a response dictionary — so a caller who wants to retry a throttle
but not a permission failure has to parse it. Translating at the boundary is what lets them write
`except ThrottledError` instead, and it is the reason this module wraps boto3 rather than handing
its clients out.

**The tree is shaped by what a caller would do next, not by which service spoke.** There is no
`S3Error` or `DynamoDbError`, because "it was S3" is already in the message and never changes the
handling. What changes the handling is whether to retry the same request (`ThrottledError`), fix
a policy (`AccessDeniedError`), re-read and retry the operation (`ConditionFailedError`), or fix
the calling code (`ItemEncodingError`).

**Three failures deliberately have no exception at all**: an object that is not in a bucket, an
item that is not in a table, and a message that is not on a queue. Absence is an ordinary answer
to a question, so it is `None` or an empty sequence. `ObjectNotFoundError` exists only for the
read that was *told* to produce bytes and could not.
"""

from dexter.commons import DexterError


class AwsError(DexterError):
    """Base class for every AWS failure."""


# ── wiring ───────────────────────────────────────────────────────────


class AwsWiringError(AwsError):
    """A registration was made in an order that cannot work.

    Raised when `register_secret_value` or `register_parameter_value` runs before `use_aws`,
    which is what binds the clients they resolve against. Without it the mistake surfaces at
    resolve time as the container reporting `SecretsManagerClient` unregistered — true, but it
    names a class rather than the call to add.
    """


# ── the request itself ───────────────────────────────────────────────


class AwsRequestError(AwsError):
    """A request to AWS was refused, or could not be made.

    Raised for anything the service returned that the caller did not ask about, and for a
    transport failure. The message names the operation and what came back, because that is the
    only thing a reader can act on.

    **It does not distinguish "did not happen" from "happened but was not confirmed."** A
    timeout on a `PutObject` may or may not have written the object; a caller retrying on this
    should assume it may already have.
    """


class ThrottledError(AwsRequestError):
    """The service asked this process to slow down.

    The one failure where retrying the *same* request unchanged is correct — everything else in
    this tree needs something to change first. botocore has already retried it up to
    `AwsConfig.max_attempts` by the time this is raised, so seeing it means the backoff budget
    ran out and the decision is now the caller's.

    Under `AwsRequestError` rather than beside it, so an existing `except AwsRequestError`
    keeps catching it.
    """


class AccessDeniedError(AwsRequestError):
    """The identity is valid and the policy does not allow this.

    Separate from `CredentialsUnavailableError` because the fix is somewhere else entirely: the
    credential chain worked, the process is who it says it is, and the permission is missing.
    That is a change to a policy document, not to a credential helper — and a deployment that
    confuses the two spends its time looking at the wrong file.
    """


class CredentialsUnavailableError(AwsError):
    """No credentials could be found, or the ones found were rejected.

    Separate from `AwsRequestError` because the fix is entirely external: nothing in the calling
    code is wrong, the process simply has no usable identity. On a host outside AWS that usually
    means the credential helper is not running — see the module docstring of
    `dexter.aws.session`.
    """


class ResourceNotFoundError(AwsError):
    """The bucket, table, queue or topic named does not exist.

    The *container*, never its contents. A missing object is an ordinary answer and a missing
    bucket is a deployment that was never provisioned, so collapsing the two would turn a broken
    deploy into what looks like an empty one.
    """


# ── stored values ────────────────────────────────────────────────────


class ObjectNotFoundError(AwsError):
    """An object was read that is not in the bucket.

    Its own class rather than an `AwsRequestError`, because "not there" is an ordinary answer to
    a question and not a fault: an upload that never completed is the common case, and a caller
    wants to treat it differently from being denied access to the bucket.

    Only `get_object` raises it, because only `get_object` was asked for bytes it could not
    produce. `head_object` answers `None`.
    """


class SecretNotFoundError(AwsError):
    """A secret, or a key inside one, does not exist.

    Covers both halves deliberately. From the caller's side "the secret is missing" and "the
    secret is there but has no `DATABASE_PASSWORD` in it" are the same mistake — a value that
    was supposed to be provisioned was not — and the message says which of the two it was.

    **The message never lists the keys that *are* present.** It reaches a log, and an inventory
    of a secret store does not belong there.
    """


class ParameterNotFoundError(AwsError):
    """A parameter does not exist in the parameter store.

    The sibling of `SecretNotFoundError`, for the same reason and with the same shape: something
    the deployment was supposed to provision was not.
    """


# ── documents ────────────────────────────────────────────────────────


class ItemEncodingError(AwsError):
    """An item holds something that cannot be stored, or read back.

    **Raised before any request is made**, which is the point of it: this is a bug in the
    calling code, not an answer from a service, and the message names the attribute path and the
    offending type so the line that built the item can be found.

    The cases are narrow and each one is a silent corruption if allowed through — a `float`
    (binary floating point is not what anybody means by a price), an empty set (which the
    service refuses with a validation error naming nothing), a `datetime` or a `UUID` (which
    have no canonical stored form, so choosing one for the caller would be choosing wrong half
    the time).
    """


class ConditionFailedError(AwsError):
    """A conditional write did not happen because its condition was false.

    **Not an `AwsRequestError`, and that distinction is the whole reason this class exists.**
    Nothing about the request was wrong: the caller said "write this only if the version is
    still 3", and it was not. That is the ordinary outcome of a lost race, and the response is
    to re-read and retry the *business* operation — not the request. Optimistic concurrency is
    unusable without a way to catch exactly this and nothing else.

    Retrying the identical request is guaranteed to fail again, which is what separates it from
    `TransactionConflictError`.
    """


class TransactionConflictError(AwsError):
    """A transaction was cancelled because another one touched the same item.

    Retryable, where `ConditionFailedError` is not. They are two classes precisely so that a
    caller can back off and retry one while re-reading for the other; one class would force
    every caller to guess.
    """


class BatchIncompleteError(AwsError):
    """A batch still had unprocessed entries after the retry budget ran out.

    DynamoDB answers a batch write with HTTP 200 and an `UnprocessedItems` map, and a client
    that does not look at it loses writes silently — under throttling, which is exactly when
    losing them matters most. This module retries them and raises when it cannot finish, so the
    failure is loud and names how many remained.
    """


# ── messaging ────────────────────────────────────────────────────────


class EmailRejectedError(AwsError):
    """The mail service refused the message.

    A verification, suppression or sending-pause problem rather than a malformed request: the
    fix is in the account's configuration, and no retry of the same message will help until it
    is made.
    """


class MessageTooLargeError(AwsError):
    """A message, or a batch of them, exceeds the service's size limit.

    Checked locally before the request, because the service answers `InvalidParameterValue`
    without saying *which* of the ten entries was too big.
    """
