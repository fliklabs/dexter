"""Recognising the failures whose meaning depends on which operation raised them.

`_calling.py` translates every code that means the same thing everywhere. These are the ones that
do not: a `ConditionalCheckFailedException` is a *lost race* whose message should name which
condition was not met, and a `TransactionCanceledException` carries its real cause in an array
nobody would think to look in.

**The two spellings of a failed condition are the trap in this file.** A single write is refused
with `ConditionalCheckFailedException`; the per-entry reasons inside a cancelled transaction use
`ConditionalCheckFailed`, without the suffix. Comparing the reasons against the exception's code
matches nothing, so every cancelled transaction would surface as a bare request error with the
cause buried.
"""

from typing import Any

from botocore.exceptions import ClientError

from .._calling import error_code
from ..errors import ConditionFailedError, TransactionConflictError
from ..session import AwsSession

CONDITION_FAILED_CODE = "ConditionalCheckFailedException"
"""What DynamoDB says when a single conditional write's condition was false."""

CANCELLED_CONDITION_CODE = "ConditionalCheckFailed"
"""The same failure, as a transaction's cancellation reason. **Not the same string.**"""

CONFLICT_CODE = "TransactionConflict"
"""What a cancellation reason says when another transaction is changing the same item."""


def translate_condition(error: ClientError, message: str, /) -> None:
    """Raise `ConditionFailedError` if this is a failed condition, otherwise return."""
    if error_code(error) == CONDITION_FAILED_CODE:
        raise ConditionFailedError(message) from error


def translate_cancellation(error: ClientError, /) -> None:
    """Turn a cancelled transaction into the specific reason it was cancelled.

    `TransactionCanceledException` carries a `CancellationReasons` array with one entry per item,
    and the entry that is not `None` is the one that matters. Without reading it a caller cannot
    tell a lost condition — which must not be retried — from a conflict, which should be.
    """
    reasons = error.response.get("CancellationReasons") or []
    for index, reason in enumerate(reasons):
        code = reason.get("Code")
        if code == CANCELLED_CONDITION_CODE:
            raise ConditionFailedError(
                f"The transaction was cancelled: the condition on entry {index} was not met."
            ) from error
        if code == CONFLICT_CODE:
            raise TransactionConflictError(
                f"The transaction was cancelled: another transaction is changing entry "
                f"{index}."
            ) from error


# The three conditional writes. Each catches `ClientError` before `_calling.call` sees it,
# because the code it is looking for means something different depending on which operation
# raised it — a shared table in `_calling.py` could not say *which* condition was not met.


def put(session: AwsSession, request: dict[str, Any], /) -> Any:
    """`PutItem`, with a lost condition named as one."""
    try:
        return session.dynamodb.put_item(**request)
    except ClientError as error:
        translate_condition(error, "The condition on the put was not met.")
        raise


def update(session: AwsSession, request: dict[str, Any], /) -> Any:
    """`UpdateItem`, with a lost condition named as one."""
    try:
        return session.dynamodb.update_item(**request)
    except ClientError as error:
        translate_condition(error, "The condition on the update was not met.")
        raise


def delete(session: AwsSession, request: dict[str, Any], /) -> Any:
    """`DeleteItem`, with a lost condition named as one."""
    try:
        return session.dynamodb.delete_item(**request)
    except ClientError as error:
        translate_condition(error, "The condition on the delete was not met.")
        raise
