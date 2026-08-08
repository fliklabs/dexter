"""Covers `dexter.aws.s3.listing`: paging, and the two ways a listing stops early.

**The pagination tests are the reason this file exists.** `ListObjectsV2` answers at most a
thousand keys and a continuation token, so a client that makes one request returns something that
looks exactly like a complete answer. Both failure modes get a named test: stopping after the
first page, and stopping on a page that happens to hold no objects.
"""

from datetime import UTC, datetime
from typing import Any

from botocore.stub import Stubber

from dexter.aws import AwsSession, S3Client

from .conftest import BUCKET

MODIFIED = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def entry(key: str, /, *, size: int = 10) -> dict[str, Any]:
    """One `Contents` entry as S3 returns it."""
    return {
        "Key": key,
        "Size": size,
        "LastModified": MODIFIED,
        "ETag": '"d41d8cd98f00b204e9800998ecf8427e"',
    }


def add_page(
    stub: Stubber,
    /,
    *,
    keys: list[str] | None = None,
    prefixes: list[str] | None = None,
    token: str | None = None,
    expects: dict[str, Any] | None = None,
) -> None:
    """Queue one `ListObjectsV2` page."""
    response: dict[str, Any] = {
        "Contents": [entry(key) for key in keys or []],
        "IsTruncated": token is not None,
    }
    if prefixes is not None:
        response["CommonPrefixes"] = [{"Prefix": prefix} for prefix in prefixes]
    if token is not None:
        response["NextContinuationToken"] = token
    stub.add_response(
        "list_objects_v2",
        response,
        expects or {"Bucket": BUCKET, "Prefix": "", "MaxKeys": 1000},
    )


class TestListing:
    async def test_yields_every_object(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(s3_stub, keys=["a", "b"])
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.key async for summary in stream] == ["a", "b"]

    async def test_reads_the_size_and_etag_of_each_entry(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(s3_stub, keys=["a"])
        stream = S3Client(session).list_objects(BUCKET)

        summaries = [summary async for summary in stream]
        assert summaries[0].size_bytes == 10
        assert summaries[0].etag == '"d41d8cd98f00b204e9800998ecf8427e"'
        assert summaries[0].last_modified == MODIFIED

    async def test_a_listing_carries_no_content_type(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """S3 does not return one, and guessing it from the key would be inventing a fact."""
        add_page(s3_stub, keys=["photo.jpg"])
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.content_type async for summary in stream] == [None]

    async def test_narrows_to_a_prefix(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(
            s3_stub,
            keys=["items/a"],
            expects={"Bucket": BUCKET, "Prefix": "items/", "MaxKeys": 1000},
        )
        stream = S3Client(session).list_objects(BUCKET, prefix="items/")

        assert [summary.key async for summary in stream] == ["items/a"]

    async def test_an_empty_bucket_yields_nothing(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(s3_stub)
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary async for summary in stream] == []


class TestPaging:
    async def test_follows_the_continuation_token(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """**The regression test for silent truncation.**

        One request returning the first thousand keys is indistinguishable, at the call site,
        from a complete listing. This is the shape that catches it.
        """
        add_page(s3_stub, keys=["a"], token="more")
        add_page(
            s3_stub,
            keys=["b"],
            expects={
                "Bucket": BUCKET,
                "Prefix": "",
                "MaxKeys": 1000,
                "ContinuationToken": "more",
            },
        )
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.key async for summary in stream] == ["a", "b"]

    async def test_a_page_with_no_objects_is_not_the_end(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """A delimiter page can hold only common prefixes. Stopping on an empty `Contents`
        rather than on a missing token is the second way to truncate."""
        add_page(s3_stub, keys=[], token="more")
        add_page(
            s3_stub,
            keys=["b"],
            expects={
                "Bucket": BUCKET,
                "Prefix": "",
                "MaxKeys": 1000,
                "ContinuationToken": "more",
            },
        )
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.key async for summary in stream] == ["b"]

    async def test_stops_when_the_answer_is_truncated_but_names_no_token(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """Should not happen, and treating it as "keep going" would be an infinite loop
        against a service that has stopped answering."""
        s3_stub.add_response(
            "list_objects_v2",
            {"Contents": [entry("a")], "IsTruncated": True},
            {"Bucket": BUCKET, "Prefix": "", "MaxKeys": 1000},
        )
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.key async for summary in stream] == ["a"]

    async def test_the_page_size_is_a_round_trip_count_not_a_cap(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """There is no `max_keys`. A smaller page means more requests and the same answer."""
        add_page(
            s3_stub,
            keys=["a"],
            token="more",
            expects={"Bucket": BUCKET, "Prefix": "", "MaxKeys": 1},
        )
        add_page(
            s3_stub,
            keys=["b"],
            expects={
                "Bucket": BUCKET,
                "Prefix": "",
                "MaxKeys": 1,
                "ContinuationToken": "more",
            },
        )
        stream = S3Client(session).list_objects(BUCKET, page_size=1)

        assert [summary.key async for summary in stream] == ["a", "b"]


class TestPages:
    async def test_yields_objects_and_prefixes_together(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(
            s3_stub,
            keys=["items/a"],
            prefixes=["items/photos/"],
            expects={
                "Bucket": BUCKET,
                "Prefix": "items/",
                "MaxKeys": 1000,
                "Delimiter": "/",
            },
        )
        stream = S3Client(session).list_objects(BUCKET, prefix="items/", delimiter="/")

        pages = [page async for page in stream.pages()]
        assert [summary.key for summary in pages[0].objects] == ["items/a"]
        assert pages[0].common_prefixes == ("items/photos/",)

    async def test_prefixes_collects_across_pages(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """How "list the folders" is spelled, and it pages like everything else."""
        add_page(
            s3_stub,
            prefixes=["a/"],
            token="more",
            expects={"Bucket": BUCKET, "Prefix": "", "MaxKeys": 1000, "Delimiter": "/"},
        )
        add_page(
            s3_stub,
            prefixes=["b/"],
            expects={
                "Bucket": BUCKET,
                "Prefix": "",
                "MaxKeys": 1000,
                "Delimiter": "/",
                "ContinuationToken": "more",
            },
        )
        stream = S3Client(session).list_objects(BUCKET, delimiter="/")

        assert await stream.prefixes() == ("a/", "b/")

    async def test_no_delimiter_means_no_prefixes(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        add_page(s3_stub, keys=["a"])
        stream = S3Client(session).list_objects(BUCKET)

        assert await stream.prefixes() == ()


class TestTheStreamItself:
    async def test_building_one_reaches_no_network(self, session: AwsSession) -> None:
        """**Why `list_objects` is not `async`.** No stub is installed, so a request would fail."""
        assert S3Client(session).list_objects(BUCKET) is not None

    async def test_iterating_again_starts_from_the_first_page(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """It holds a query, not a result. Two passes are two listings."""
        add_page(s3_stub, keys=["a"])
        add_page(s3_stub, keys=["a"])
        stream = S3Client(session).list_objects(BUCKET)

        assert [summary.key async for summary in stream] == ["a"]
        assert [summary.key async for summary in stream] == ["a"]
