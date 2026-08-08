"""Walking the objects under a prefix, without stopping early.

**`ListObjectsV2` answers at most a thousand keys and says so with a continuation token**, and a
client that makes one request and returns what came back is indistinguishable, at the call site,
from one that listed the whole bucket. That is the failure this file exists to make impossible:
there is no `max_keys` argument to cap the answer, and nothing here returns a list.

`ObjectStream` is returned by a **synchronous** `list_objects`, which is the one non-`async`
public method in the module. It performs no I/O when it is built — the first request happens when
iteration starts — so there is nothing to await. The alternative, an `async def` returning a list,
is the truncating version wearing a coroutine.
"""

from collections.abc import AsyncIterator
from typing import Any

from .._calling import call
from ..models import ObjectPage, ObjectSummary
from ..session import AwsSession


class ObjectStream:
    """Every object under a prefix, fetched a page at a time as it is consumed.

    Iterate it for objects, or call `pages()` when the grouping matters — which it does for the
    delimiter case, where `common_prefixes` is the answer and the objects are noise.

    **Re-iterating starts again from the first page.** It holds a query, not a result, so a
    caller who needs the objects twice should keep them.
    """

    __slots__ = ("_bucket", "_delimiter", "_page_size", "_prefix", "_session")

    def __init__(
        self,
        session: AwsSession,
        bucket: str,
        *,
        prefix: str = "",
        delimiter: str | None = None,
        page_size: int = 1000,
    ) -> None:
        """Record the query. Nothing is requested until iteration begins."""
        self._session = session
        self._bucket = bucket
        self._prefix = prefix
        self._delimiter = delimiter
        self._page_size = page_size

    async def __aiter__(self) -> AsyncIterator[ObjectSummary]:
        """Every object under the prefix, in the order S3 returns them.

        Raises:
            ResourceNotFoundError: If the bucket does not exist.
            AwsRequestError: If a listing was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        async for page in self.pages():
            for summary in page.objects:
                yield summary

    async def pages(self) -> AsyncIterator[ObjectPage]:
        """Each response in turn, objects and common prefixes together.

        **Stops when S3 stops giving a continuation token, never when a page looks empty.** A
        listing whose page contains only common prefixes has no objects in it and is not the
        last page, so "stop when the page is empty" silently truncates.
        """
        token: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": self._prefix,
                "MaxKeys": self._page_size,
            }
            if self._delimiter is not None:
                arguments["Delimiter"] = self._delimiter
            if token is not None:
                arguments["ContinuationToken"] = token

            response = await self._page(arguments)
            yield ObjectPage(
                objects=tuple(
                    _summary(entry) for entry in response.get("Contents", [])
                ),
                common_prefixes=tuple(
                    entry["Prefix"] for entry in response.get("CommonPrefixes", [])
                ),
            )

            if not response.get("IsTruncated"):
                return
            token = response.get("NextContinuationToken")
            if not token:
                # Truncated but tokenless should not happen; treating it as "keep going" would
                # be an infinite loop against a service that has stopped answering.
                return

    async def prefixes(self) -> tuple[str, ...]:
        """Every common prefix under the query, which is how "list the folders" is spelled.

        Only meaningful with a delimiter; without one S3 returns none, and this is empty.
        """
        found: list[str] = []
        async for page in self.pages():
            found.extend(page.common_prefixes)
        return tuple(found)

    async def _page(self, arguments: dict[str, Any], /) -> Any:
        """One listing request.

        Its own method rather than a lambda built inside the loop above, because a closure over
        a loop variable is what ruff's B023 exists to catch.
        """
        return await call(
            f"ListObjectsV2 s3://{self._bucket}/{self._prefix}",
            lambda: self._session.s3.list_objects_v2(**arguments),
        )


def _summary(entry: Any, /) -> ObjectSummary:
    """One listing entry as an `ObjectSummary`.

    A listing carries no content type — only a HEAD does — so it is `None` here rather than
    guessed from the key's extension, which would be dexter inventing a fact.
    """
    return ObjectSummary(
        key=entry["Key"],
        size_bytes=entry.get("Size", 0),
        content_type=None,
        last_modified=entry["LastModified"],
        etag=entry.get("ETag"),
    )
