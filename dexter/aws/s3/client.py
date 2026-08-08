"""The operations a caller reaches for, over one bucket at a time.

The bucket is an argument rather than configuration, because an application that stores two
unrelated kinds of thing should not need two of these — and because the bucket is usually the
part that varies per deployment while the client does not.

**Several methods here are three lines.** Listing, presigning, copying and batched deletion each
have their own file, and this one is the surface a consumer reads: what can be done, what each
answer means, and what is raised. The decision that stays here is the one that recurs —
`head_object` answers `None` where `get_object` raises, because "did the upload happen" has
absence as an ordinary answer and "give me the bytes" does not.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from botocore.exceptions import ClientError

from .._calling import call
from ..errors import ObjectNotFoundError
from ..models import DeleteReport, ObjectSummary
from ..session import AwsSession
from ._failures import is_missing
from .copying import copy_object, move_object
from .deleting import delete_objects
from .listing import ObjectStream
from .presigning import presigned_get_url, presigned_put_url
from .tagging import get_object_tags, put_object_tags


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
                if is_missing(error):
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
                if is_missing(error):
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
            an exception has been told nothing about them. See `dexter.aws.s3.deleting`.

        Raises:
            AwsRequestError: If a request was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await delete_objects(self._session, bucket, keys)

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

        Giving `content_type` tells S3 to *replace* the metadata rather than copy it, which is
        the only way to change it — see `dexter.aws.s3.copying`.

        Raises:
            ObjectNotFoundError: If the source object does not exist.
            AwsRequestError: If the copy was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await copy_object(
            self._session,
            source_bucket,
            source_key,
            target_bucket,
            target_key,
            content_type=content_type,
        )

    async def move_object(self, bucket: str, source_key: str, target_key: str) -> None:
        """Move an object within one bucket, by copying and then deleting.

        **S3 has no move**, so this is two operations and cannot be atomic — but the delete runs
        only once the copy is confirmed, so an interruption leaves the object under both keys
        rather than under neither. See `dexter.aws.s3.copying`.

        Raises:
            ObjectNotFoundError: If the source does not exist, or the copy was not confirmed.
            AwsRequestError: If either half was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await move_object(self._session, bucket, source_key, target_key)

    async def get_object_tags(self, bucket: str, key: str) -> dict[str, str]:
        """The object's tags, as a plain mapping.

        Raises:
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await get_object_tags(self._session, bucket, key)

    async def put_object_tags(
        self, bucket: str, key: str, tags: Mapping[str, str]
    ) -> None:
        """Replace the object's tags with `tags`.

        **Replaces rather than merges** — an empty mapping clears them. See
        `dexter.aws.s3.tagging` for why there is no "add one tag" helper.

        Raises:
            AwsRequestError: If the write was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        await put_object_tags(self._session, bucket, key, tags)

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
