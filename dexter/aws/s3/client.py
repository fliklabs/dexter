"""Reading and writing objects, and the operations that move or describe them.

The bucket is an argument rather than configuration, because an application that stores two
unrelated kinds of thing should not need two of these — and because the bucket is usually the
part that varies per deployment while the client does not.

Two decisions here are worth knowing before reading the code:

- **`head_object` answers `None` and `get_object` raises.** Both are right. "Did the upload
  happen" has absence as an ordinary answer, and `None` keeps that on the normal path; "give me
  the bytes" was told to produce something and could not.
- **`delete_objects` returns a report rather than nothing.** `DeleteObjects` answers HTTP 200
  with the refused keys listed in the body, so a signature returning `None` throws away the only
  part a caller has to act on.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from botocore.exceptions import ClientError

from .._calling import call, error_code
from ..errors import ObjectNotFoundError
from ..models import DeleteFailure, DeleteReport, ObjectSummary
from ..session import AwsSession
from .listing import ObjectStream
from .presigning import presigned_get_url, presigned_put_url

MISSING_OBJECT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
"""What S3 says when an object is not there, which is three different things.

`GetObject` answers `NoSuchKey`. `HeadObject` has no body to put a code in, so botocore
synthesises one from the status and it arrives as `404` — or as `NotFound`, depending on the
version. Matching one of the three and letting the others through is how a missing object comes
back as a permission error.
"""

DELETE_BATCH_SIZE = 1000
"""How many keys one `DeleteObjects` may carry. A hard service limit, not a choice."""


class S3Client:
    """Object storage: reading, writing, describing, moving and listing."""

    __slots__ = ("_session",)

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients."""
        self._session = session

    async def put_object(  # noqa: PLR0913 - each header is a separate thing S3 stores
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        cache_control: str | None = None,
    ) -> None:
        """Write `body` to `key`.

        Args:
            bucket: The bucket to write into.
            key: The object key. It should carry no user-supplied segment — a key built from
                something a caller typed is a path traversal and an enumeration in one.
            body: The bytes to store.
            content_type: What the object is. Worth setting even when nothing reads it back
                through this client: it is what a browser follows when it opens a presigned
                `GET`, and an image stored without one is offered as a download.
            metadata: Names and values stored beside the object and returned by a HEAD. S3
                prefixes them with `x-amz-meta-` on the wire; the names here are the bare ones.
            cache_control: The `Cache-Control` header S3 serves the object with, which is what a
                CDN in front of a presigned `GET` obeys.

        Raises:
            AwsRequestError: If the write was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": body,
            # Substituted rather than omitted. `application/octet-stream` is precisely what S3
            # records when `ContentType` is absent, so this changes no behaviour — and it removes
            # the alternative, which was splatting a conditionally-built dictionary into a call
            # whose stubs type every keyword separately and reject it ten different ways.
            "ContentType": content_type or "application/octet-stream",
        }
        if metadata:
            request["Metadata"] = dict(metadata)
        if cache_control is not None:
            request["CacheControl"] = cache_control

        await call(
            f"PutObject s3://{bucket}/{key}",
            lambda: self._session.s3.put_object(**request),
        )

    async def get_object(self, bucket: str, key: str) -> bytes:
        """Read the whole object at `key`.

        **Reads it entirely into memory**, which is the right shape for the photographs and
        small documents this exists for and the wrong one for anything large. A caller streaming
        gigabytes wants a presigned URL and its own HTTP client, not this method.

        Raises:
            ObjectNotFoundError: If there is no object at `key`.
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """

        def read() -> bytes:
            try:
                response = self._session.s3.get_object(Bucket=bucket, Key=key)
            except ClientError as error:
                if error_code(error) in MISSING_OBJECT_CODES:
                    raise ObjectNotFoundError(
                        f"s3://{bucket}/{key} does not exist."
                    ) from error
                raise
            # Reading the stream is part of the call rather than something the caller does
            # later: it is blocking I/O, and outside this thread it would be on the loop.
            return response["Body"].read()

        return await call(f"GetObject s3://{bucket}/{key}", read)

    async def head_object(self, bucket: str, key: str) -> ObjectSummary | None:
        """Describe the object at `key`, or answer `None` if there is none.

        **`None` rather than an exception, and that is the whole design of it.** This is the
        question "did the upload actually happen", asked about a `PUT` some browser made through
        a presigned URL. Absent is the ordinary answer to that question — an upload abandoned
        halfway is the common case — and an exception would make the caller write a `try` around
        the normal path.

        Raises:
            AwsRequestError: If the request was refused for any reason other than the object
                being absent.
            CredentialsUnavailableError: If this process has no usable identity.
        """

        def describe() -> ObjectSummary | None:
            try:
                response = self._session.s3.head_object(Bucket=bucket, Key=key)
            except ClientError as error:
                if error_code(error) in MISSING_OBJECT_CODES:
                    return None
                raise
            return ObjectSummary(
                key=key,
                size_bytes=response["ContentLength"],
                content_type=response.get("ContentType"),
                last_modified=response["LastModified"],
                etag=response.get("ETag"),
            )

        return await call(f"HeadObject s3://{bucket}/{key}", describe)

    async def delete_object(self, bucket: str, key: str) -> None:
        """Remove the object at `key`.

        **Deleting something that is not there succeeds**, and that is S3's behaviour rather than
        a decision made here. It is also the useful one: a cleanup that has already partly run
        should be safe to run again.

        Raises:
            AwsRequestError: If the delete was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await call(
            f"DeleteObject s3://{bucket}/{key}",
            lambda: self._session.s3.delete_object(Bucket=bucket, Key=key),
        )

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> DeleteReport:
        """Remove many objects, a thousand at a time.

        Returns:
            Which keys were removed and which were refused. **Check `failed`** — the request
            succeeds even when individual keys do not, so a caller who reads only the absence of
            an exception has been told nothing about them.

        Raises:
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        deleted: list[str] = []
        failed: list[DeleteFailure] = []
        for start in range(0, len(keys), DELETE_BATCH_SIZE):
            chunk = keys[start : start + DELETE_BATCH_SIZE]
            # Chunks go one after another rather than through a `gather`. Forty concurrent
            # chunks would be forty threads against an executor that holds about thirty-two,
            # which is a self-inflicted stall rather than parallelism.
            response = await self._delete_chunk(bucket, chunk)
            deleted.extend(entry["Key"] for entry in response.get("Deleted", []))
            failed.extend(
                DeleteFailure(
                    key=entry.get("Key", ""),
                    code=entry.get("Code", ""),
                    message=entry.get("Message", ""),
                )
                for entry in response.get("Errors", [])
            )
        return DeleteReport(deleted=tuple(deleted), failed=tuple(failed))

    async def copy_object(
        self,
        source_bucket: str,
        source_key: str,
        target_bucket: str,
        target_key: str,
        *,
        content_type: str | None = None,
    ) -> None:
        """Copy one object to another key, possibly in another bucket.

        Args:
            source_bucket: Where the object is now.
            source_key: The object to copy.
            target_bucket: Where it should end up.
            target_key: The key it should end up under.
            content_type: A replacement content type. Given one, S3 is told to replace the
                metadata rather than copy it, which is the only way to change it — an ordinary
                copy carries the source's across.

        Raises:
            ObjectNotFoundError: If the source object does not exist.
            AwsRequestError: If the copy was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await self._copy(
            source_bucket, source_key, target_bucket, target_key, content_type
        )

    async def move_object(self, bucket: str, source_key: str, target_key: str) -> None:
        """Move an object within one bucket, by copying and then deleting.

        **S3 has no move**, so this is two operations and cannot be atomic. What it does
        guarantee is the direction of the failure: the delete only runs once the copy has been
        confirmed, so an interruption leaves the object under both keys rather than under
        neither. Copying and deleting unconditionally is how the object is lost — a copy can
        answer 200 with a failure in its body.

        Raises:
            ObjectNotFoundError: If the source object does not exist.
            AwsRequestError: If either half was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        response = await self._copy(bucket, source_key, bucket, target_key, None)
        if not response.get("CopyObjectResult"):
            raise ObjectNotFoundError(
                f"s3://{bucket}/{source_key} was not copied to {target_key}, so it has "
                f"not been deleted."
            )
        await self.delete_object(bucket, source_key)

    async def get_object_tags(self, bucket: str, key: str) -> dict[str, str]:
        """The object's tags, as a plain mapping.

        Raises:
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        response = await call(
            f"GetObjectTagging s3://{bucket}/{key}",
            lambda: self._session.s3.get_object_tagging(Bucket=bucket, Key=key),
        )
        return {tag["Key"]: tag["Value"] for tag in response.get("TagSet", [])}

    async def put_object_tags(
        self, bucket: str, key: str, tags: Mapping[str, str]
    ) -> None:
        """Replace the object's tags with `tags`.

        **Replaces rather than merges**, which is the operation S3 offers and the one worth
        exposing. A read-modify-write "add one tag" helper would look convenient and would lose
        a concurrent tag every time two callers ran it at once; a caller who wants that reads
        the tags, changes them, and writes them back where the race is visible.

        Raises:
            AwsRequestError: If the write was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        tag_set = [{"Key": name, "Value": value} for name, value in tags.items()]
        await call(
            f"PutObjectTagging s3://{bucket}/{key}",
            lambda: self._session.s3.put_object_tagging(
                Bucket=bucket,
                Key=key,
                Tagging={"TagSet": tag_set},  # type: ignore[typeddict-item]
            ),
        )

    def list_objects(
        self,
        bucket: str,
        *,
        prefix: str = "",
        delimiter: str | None = None,
        page_size: int = 1000,
    ) -> ObjectStream:
        """Every object under `prefix`, as a stream that pages.

        **The one method here that is not `async`, and the one that cannot truncate.** It builds
        a query and reaches no network, so there is nothing to await; the requests happen as the
        stream is consumed. There is deliberately no `max_keys` argument — a listing that
        silently stops at a thousand keys looks exactly like a complete one.

        Args:
            bucket: The bucket to list.
            prefix: Only keys starting with this. Empty lists the whole bucket.
            delimiter: Groups keys sharing a prefix up to this character instead of returning
                them, which is how "list the folders" is spelled. Read `pages()` or `prefixes()`
                when using it — the objects are not the answer.
            page_size: How many keys to ask for per request. A thousand is the service maximum
                and the sensible default; it changes the number of round trips, never the
                result.
        """
        return ObjectStream(
            self._session,
            bucket,
            prefix=prefix,
            delimiter=delimiter,
            page_size=page_size,
        )

    async def presigned_get_url(
        self,
        bucket: str,
        key: str,
        *,
        expires_in_seconds: int = 3600,
        filename: str | None = None,
    ) -> str:
        """A URL that reads `key`. See `dexter.aws.s3.presigning`."""
        return await presigned_get_url(
            self._session,
            bucket,
            key,
            expires_in_seconds=expires_in_seconds,
            filename=filename,
        )

    async def presigned_put_url(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        expires_in_seconds: int = 3600,
    ) -> str:
        """A URL that writes `key`. See `dexter.aws.s3.presigning`."""
        return await presigned_put_url(
            self._session,
            bucket,
            key,
            content_type=content_type,
            expires_in_seconds=expires_in_seconds,
        )

    async def _delete_chunk(self, bucket: str, keys: Sequence[str], /) -> Any:
        """Delete one chunk of at most a thousand keys.

        Its own method rather than a lambda built inside the loop, because a closure over a loop
        variable is what ruff's B023 exists to catch.
        """
        return await call(
            f"DeleteObjects s3://{bucket}",
            lambda: self._session.s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in keys]},
            ),
        )

    async def _copy(
        self,
        source_bucket: str,
        source_key: str,
        target_bucket: str,
        target_key: str,
        content_type: str | None,
        /,
    ) -> Any:
        """The shared half of `copy_object` and `move_object`."""
        request: dict[str, Any] = {
            "Bucket": target_bucket,
            "Key": target_key,
            "CopySource": {"Bucket": source_bucket, "Key": source_key},
        }
        if content_type is not None:
            # Without `REPLACE`, S3 copies the source's metadata and ignores this entirely —
            # succeeding while changing nothing, which is the worst of the available outcomes.
            request["ContentType"] = content_type
            request["MetadataDirective"] = "REPLACE"

        def copy() -> Any:
            try:
                return self._session.s3.copy_object(**request)
            except ClientError as error:
                if error_code(error) in MISSING_OBJECT_CODES:
                    raise ObjectNotFoundError(
                        f"s3://{source_bucket}/{source_key} does not exist."
                    ) from error
                raise

        return await call(
            f"CopyObject s3://{source_bucket}/{source_key} to "
            f"s3://{target_bucket}/{target_key}",
            copy,
        )
