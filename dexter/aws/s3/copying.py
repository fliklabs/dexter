"""Copying an object, and the two-step that stands in for a move.

**S3 has no move**, so `move_object` is a copy and then a delete, and cannot be atomic. What it
can guarantee is the *direction* of the failure, and that is the whole reason this is its own
file rather than four lines inside the client: the delete runs only once the copy has been
confirmed, so an interruption leaves the object under both keys rather than under neither.

Copying and deleting unconditionally is how the object is lost, and it is an easy mistake to make
because the copy appears to have succeeded — `CopyObject` can answer HTTP 200 with a failure in
its body, which is the same trap `DeleteObjects` sets and the reason both are handled deliberately.
"""

from typing import Any

from botocore.exceptions import ClientError

from .._calling import call
from ..errors import ObjectNotFoundError
from ..session import AwsSession
from ._failures import is_missing


async def copy_object(  # noqa: PLR0913 - two bucket-and-key pairs is four of these six
    session: AwsSession,
    source_bucket: str,
    source_key: str,
    target_bucket: str,
    target_key: str,
    *,
    content_type: str | None = None,
) -> Any:
    """Copy one object to another key, possibly in another bucket.

    Args:
        session: The boto3 clients to copy through.
        source_bucket: Where the object is now.
        source_key: The object to copy.
        target_bucket: Where it should end up.
        target_key: The key it should end up under.
        content_type: A replacement content type. Given one, S3 is told to *replace* the metadata
            rather than copy it — which is the only way to change it, because without
            `MetadataDirective` an ordinary copy carries the source's across and ignores this
            entirely, succeeding while changing nothing.

    Returns:
        The raw response, so a caller that has to check `CopyObjectResult` can.

    Raises:
        ObjectNotFoundError: If the source object does not exist.
        AwsRequestError: If the copy was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    request: dict[str, Any] = {
        "Bucket": target_bucket,
        "Key": target_key,
        "CopySource": {"Bucket": source_bucket, "Key": source_key},
    }
    if content_type is not None:
        request["ContentType"] = content_type
        request["MetadataDirective"] = "REPLACE"

    def copy() -> Any:
        try:
            return session.s3.copy_object(**request)
        except ClientError as error:
            if is_missing(error):
                raise ObjectNotFoundError(
                    f"s3://{source_bucket}/{source_key} does not exist."
                ) from error
            raise

    return await call(
        f"CopyObject s3://{source_bucket}/{source_key} to "
        f"s3://{target_bucket}/{target_key}",
        copy,
    )


async def move_object(
    session: AwsSession, bucket: str, source_key: str, target_key: str
) -> None:
    """Move an object within one bucket, by copying and then deleting.

    Raises:
        ObjectNotFoundError: If the source object does not exist, or if the copy was not
            confirmed — in which case **nothing has been deleted**, which is the recoverable
            half of the two ways this can go wrong.
        AwsRequestError: If either half was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    response = await copy_object(session, bucket, source_key, bucket, target_key)
    if not response.get("CopyObjectResult"):
        raise ObjectNotFoundError(
            f"s3://{bucket}/{source_key} was not copied to {target_key}, so it has "
            f"not been deleted."
        )
    await call(
        f"DeleteObject s3://{bucket}/{source_key}",
        lambda: session.s3.delete_object(Bucket=bucket, Key=source_key),
    )
