"""Reading and replacing an object's tags.

**Replacing rather than merging**, which is the operation S3 offers and the one worth exposing.
A read-modify-write "add one tag" helper would look convenient and would lose a concurrent tag
every time two callers ran it at once — the write sends the whole set, so whichever caller writes
second erases what the first added.

A caller who wants that reads the tags, changes them, and writes them back, where the race is
visible in their own code and they can decide what to do about it.
"""

from collections.abc import Mapping

from .._calling import call
from ..session import AwsSession


async def get_object_tags(session: AwsSession, bucket: str, key: str) -> dict[str, str]:
    """The object's tags, as a plain mapping.

    Raises:
        AwsRequestError: If the read was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    response = await call(
        f"GetObjectTagging s3://{bucket}/{key}",
        lambda: session.s3.get_object_tagging(Bucket=bucket, Key=key),
    )
    return {tag["Key"]: tag["Value"] for tag in response.get("TagSet", [])}


async def put_object_tags(
    session: AwsSession, bucket: str, key: str, tags: Mapping[str, str]
) -> None:
    """Replace the object's tags with `tags`.

    An empty mapping clears them, which is what "replace" means and the only way to remove one.

    Raises:
        AwsRequestError: If the write was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    tag_set = [{"Key": name, "Value": value} for name, value in tags.items()]
    await call(
        f"PutObjectTagging s3://{bucket}/{key}",
        lambda: session.s3.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={"TagSet": tag_set},  # type: ignore[typeddict-item]
        ),
    )
