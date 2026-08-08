"""Covers `dexter.aws.s3`: the six operations, and what each does when S3 says no.

The presigning tests are the ones worth reading. They assert properties of the URL rather than
its bytes — a signature is not reproducible across botocore versions, but what has to be *in*
it is stable, and that is what a browser upload actually depends on.
"""

import io
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from dexter.aws import (
    AwsRequestError,
    AwsSession,
    CredentialsUnavailableError,
    ObjectNotFoundError,
    S3Client,
)

from .conftest import BUCKET

KEY = "inventory/item-1/photo-0.jpg"
BODY = b"\xff\xd8\xff\xe0 not really a jpeg"


def make_stream(payload: bytes, /) -> StreamingBody:
    """A response body shaped the way botocore hands one back."""
    return StreamingBody(io.BytesIO(payload), len(payload))


class TestPutObject:
    async def test_writes_the_body_under_the_key(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": BODY,
                "ContentType": "image/jpeg",
            },
        )
        await S3Client(session).put_object(BUCKET, KEY, BODY, content_type="image/jpeg")

    async def test_substitutes_a_content_type_when_none_is_given(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        # The expected parameters are what makes this an assertion rather than a smoke test:
        # the stubber fails the call if `ContentType` is absent or different.
        s3_stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": BODY,
                "ContentType": "application/octet-stream",
            },
        )
        await S3Client(session).put_object(BUCKET, KEY, BODY)

    async def test_raises_when_the_write_is_refused(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "put_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError, match="AccessDenied"):
            await S3Client(session).put_object(BUCKET, KEY, BODY)


class TestGetObject:
    async def test_returns_the_stored_bytes(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "get_object", {"Body": make_stream(BODY)}, {"Bucket": BUCKET, "Key": KEY}
        )
        assert await S3Client(session).get_object(BUCKET, KEY) == BODY

    async def test_raises_object_not_found_when_the_key_is_absent(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "get_object", service_error_code="NoSuchKey", http_status_code=404
        )
        with pytest.raises(ObjectNotFoundError, match=KEY):
            await S3Client(session).get_object(BUCKET, KEY)

    async def test_a_denial_is_not_reported_as_a_missing_object(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """The distinction the whole error tree exists for.

        A bucket policy that forbids reading answers 403, and reading that as "not there" is
        how a misconfigured deployment looks like an empty one.
        """
        s3_stub.add_client_error(
            "get_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError):
            await S3Client(session).get_object(BUCKET, KEY)


class TestHeadObject:
    async def test_describes_an_object_that_exists(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        modified = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
        s3_stub.add_response(
            "head_object",
            {
                "ContentLength": len(BODY),
                "ContentType": "image/jpeg",
                "LastModified": modified,
            },
            {"Bucket": BUCKET, "Key": KEY},
        )
        summary = await S3Client(session).head_object(BUCKET, KEY)

        assert summary is not None
        assert summary.key == KEY
        assert summary.size_bytes == len(BODY)
        assert summary.content_type == "image/jpeg"
        assert summary.last_modified == modified

    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
    async def test_answers_none_for_every_spelling_of_absent(
        self, session: AwsSession, s3_stub: Stubber, code: str
    ) -> None:
        """Three codes mean the same thing, and which arrives depends on the botocore version."""
        s3_stub.add_client_error(
            "head_object", service_error_code=code, http_status_code=404
        )
        assert await S3Client(session).head_object(BUCKET, KEY) is None

    async def test_raises_rather_than_answering_none_when_denied(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "head_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError):
            await S3Client(session).head_object(BUCKET, KEY)


class TestDeleteObject:
    async def test_removes_the_object(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": KEY})
        await S3Client(session).delete_object(BUCKET, KEY)

    async def test_raises_when_the_delete_is_refused(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "delete_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError):
            await S3Client(session).delete_object(BUCKET, KEY)


class TestPresigning:
    async def test_a_get_url_names_the_object_and_carries_a_signature(
        self, session: AwsSession
    ) -> None:
        url = await S3Client(session).presigned_get_url(BUCKET, KEY)
        query = parse_qs(urlparse(url).query)

        assert BUCKET in url
        assert "photo-0.jpg" in url
        assert query["X-Amz-Signature"]
        assert query["X-Amz-Expires"] == ["3600"]

    async def test_a_get_url_asks_for_a_download_only_when_a_filename_is_given(
        self, session: AwsSession
    ) -> None:
        """An image meant to be rendered must not be signed as an attachment."""
        client = S3Client(session)

        plain = parse_qs(urlparse(await client.presigned_get_url(BUCKET, KEY)).query)
        named = parse_qs(
            urlparse(
                await client.presigned_get_url(BUCKET, KEY, filename="photo.jpg")
            ).query
        )

        assert "response-content-disposition" not in plain
        assert named["response-content-disposition"] == [
            'attachment; filename="photo.jpg"'
        ]

    async def test_a_put_url_signs_the_content_type(self, session: AwsSession) -> None:
        """The signed headers are the contract the browser has to honour.

        Whatever performs the upload must send exactly this `Content-Type`, or S3 refuses the
        signature — as an opaque 403 that carries no CORS headers, so the browser reports a
        network error and never the real cause.
        """
        url = await S3Client(session).presigned_put_url(
            BUCKET, KEY, content_type="image/jpeg"
        )
        query = parse_qs(urlparse(url).query)

        assert "content-type" in query["X-Amz-SignedHeaders"][0]
        assert query["X-Amz-Signature"]

    async def test_the_expiry_is_honoured(self, session: AwsSession) -> None:
        url = await S3Client(session).presigned_put_url(
            BUCKET, KEY, content_type="image/jpeg", expires_in_seconds=900
        )
        assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == ["900"]

    async def test_signing_reports_a_missing_identity_as_such(
        self, session: AwsSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No credentials is a deployment fault, not a bad request, and says so."""
        monkeypatch.delenv("AWS_ACCESS_KEY_ID")
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
        monkeypatch.delenv("AWS_SESSION_TOKEN")

        with pytest.raises(CredentialsUnavailableError):
            await S3Client(AwsSession(session.config)).presigned_get_url(BUCKET, KEY)


class TestPutObjectExtras:
    async def test_stores_metadata_and_cache_control(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": BODY,
                "ContentType": "image/jpeg",
                "Metadata": {"item": "1"},
                "CacheControl": "max-age=31536000",
            },
        )
        await S3Client(session).put_object(
            BUCKET,
            KEY,
            BODY,
            content_type="image/jpeg",
            metadata={"item": "1"},
            cache_control="max-age=31536000",
        )

    async def test_omits_the_optional_headers_when_unset(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """The stubber fails the call if a key it was not told about appears."""
        s3_stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": BODY,
                "ContentType": "application/octet-stream",
            },
        )
        await S3Client(session).put_object(BUCKET, KEY, BODY)


class TestDeleteObjects:
    async def test_reports_every_deleted_key(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "delete_objects",
            {"Deleted": [{"Key": "a"}, {"Key": "b"}]},
            {"Bucket": BUCKET, "Delete": {"Objects": [{"Key": "a"}, {"Key": "b"}]}},
        )

        report = await S3Client(session).delete_objects(BUCKET, ["a", "b"])

        assert report.deleted == ("a", "b")
        assert report.failed == ()

    async def test_reports_a_partial_failure_rather_than_raising(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """**The whole reason this returns a report.**

        `DeleteObjects` answers 200 with the refused keys inside the body, so a method returning
        `None` tells a caller nothing about the objects that are still there.
        """
        s3_stub.add_response(
            "delete_objects",
            {
                "Deleted": [{"Key": "a"}],
                "Errors": [
                    {"Key": "b", "Code": "AccessDenied", "Message": "denied"},
                ],
            },
            {"Bucket": BUCKET, "Delete": {"Objects": [{"Key": "a"}, {"Key": "b"}]}},
        )

        report = await S3Client(session).delete_objects(BUCKET, ["a", "b"])

        assert report.deleted == ("a",)
        assert [failure.key for failure in report.failed] == ["b"]
        assert report.failed[0].code == "AccessDenied"

    async def test_chunks_at_the_service_limit_of_a_thousand(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """One thousand and one keys is two requests. Sending them together is refused."""
        keys = [f"k{index}" for index in range(1001)]
        for chunk in (keys[:1000], keys[1000:]):
            s3_stub.add_response(
                "delete_objects",
                {"Deleted": [{"Key": key} for key in chunk]},
                {
                    "Bucket": BUCKET,
                    "Delete": {"Objects": [{"Key": key} for key in chunk]},
                },
            )

        report = await S3Client(session).delete_objects(BUCKET, keys)
        assert len(report.deleted) == 1001

    async def test_deleting_nothing_makes_no_request(self, session: AwsSession) -> None:
        """No stub is installed, so a request would fail."""
        report = await S3Client(session).delete_objects(BUCKET, [])
        assert report.deleted == ()


class TestCopyObject:
    async def test_copies_to_another_key(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "copy_object",
            {"CopyObjectResult": {"ETag": '"abc"'}},
            {
                "Bucket": BUCKET,
                "Key": "target",
                "CopySource": {"Bucket": BUCKET, "Key": KEY},
            },
        )
        await S3Client(session).copy_object(BUCKET, KEY, BUCKET, "target")

    async def test_replacing_the_content_type_says_so(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """Without `REPLACE`, S3 copies the source's metadata and ignores the new type —
        succeeding while changing nothing."""
        s3_stub.add_response(
            "copy_object",
            {"CopyObjectResult": {"ETag": '"abc"'}},
            {
                "Bucket": BUCKET,
                "Key": "target",
                "CopySource": {"Bucket": BUCKET, "Key": KEY},
                "ContentType": "image/webp",
                "MetadataDirective": "REPLACE",
            },
        )
        await S3Client(session).copy_object(
            BUCKET, KEY, BUCKET, "target", content_type="image/webp"
        )

    async def test_a_missing_source_is_reported_as_absent(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "copy_object", service_error_code="NoSuchKey", http_status_code=404
        )
        with pytest.raises(ObjectNotFoundError, match=KEY):
            await S3Client(session).copy_object(BUCKET, KEY, BUCKET, "target")


class TestMoveObject:
    async def test_copies_then_deletes(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "copy_object",
            {"CopyObjectResult": {"ETag": '"abc"'}},
            {
                "Bucket": BUCKET,
                "Key": "target",
                "CopySource": {"Bucket": BUCKET, "Key": KEY},
            },
        )
        s3_stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": KEY})

        await S3Client(session).move_object(BUCKET, KEY, "target")

    async def test_does_not_delete_when_the_copy_was_not_confirmed(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """**The failure direction that matters.**

        A copy can answer 200 with a failure in its body. Deleting unconditionally on that is
        how the object is lost; leaving it under both keys is recoverable.

        Only one response is queued, so a delete would raise — and `assert_no_pending_responses`
        catches the reverse.
        """
        s3_stub.add_response(
            "copy_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": "target",
                "CopySource": {"Bucket": BUCKET, "Key": KEY},
            },
        )

        with pytest.raises(ObjectNotFoundError, match="not been deleted"):
            await S3Client(session).move_object(BUCKET, KEY, "target")


class TestTags:
    async def test_reads_tags_as_a_mapping(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "get_object_tagging",
            {"TagSet": [{"Key": "state", "Value": "pending"}]},
            {"Bucket": BUCKET, "Key": KEY},
        )
        assert await S3Client(session).get_object_tags(BUCKET, KEY) == {
            "state": "pending"
        }

    async def test_an_untagged_object_reads_as_an_empty_mapping(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "get_object_tagging", {"TagSet": []}, {"Bucket": BUCKET, "Key": KEY}
        )
        assert await S3Client(session).get_object_tags(BUCKET, KEY) == {}

    async def test_writes_the_whole_tag_set(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_response(
            "put_object_tagging",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Tagging": {"TagSet": [{"Key": "state", "Value": "done"}]},
            },
        )
        await S3Client(session).put_object_tags(BUCKET, KEY, {"state": "done"})

    async def test_writing_an_empty_mapping_clears_the_tags(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        """It replaces rather than merges, so this is how tags are removed."""
        s3_stub.add_response(
            "put_object_tagging",
            {},
            {"Bucket": BUCKET, "Key": KEY, "Tagging": {"TagSet": []}},
        )
        await S3Client(session).put_object_tags(BUCKET, KEY, {})


class TestCopyFailures:
    async def test_a_denial_on_copy_is_not_reported_as_absent(
        self, session: AwsSession, s3_stub: Stubber
    ) -> None:
        s3_stub.add_client_error(
            "copy_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError):
            await S3Client(session).copy_object(BUCKET, KEY, BUCKET, "target")
