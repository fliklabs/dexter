"""Covers `dexter.aws._calling`: what each kind of botocore failure becomes.

Driven directly rather than through a client. Inducing a real credential failure means changing
the ambient AWS configuration, which is environment-dependent and therefore the wrong way to
assert a mapping that is meant to be exact.
"""

import threading

import pytest
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
)

from dexter.aws import AwsRequestError, CredentialsUnavailableError
from dexter.aws._calling import call, error_code
from dexter.aws.errors import AwsError

from .helpers import client_error


class TestSuccess:
    async def test_returns_what_the_work_returned(self) -> None:
        assert await call("Noop", lambda: 42) == 42

    async def test_runs_the_work_off_the_event_loop(self) -> None:
        """The whole reason this wrapper exists rather than a bare call.

        boto3 blocks, so a call made on the loop stalls every other task in the process. The
        thread identity is the only observable difference, so it is what is asserted.
        """
        loop_thread = threading.get_ident()
        assert await call("Noop", threading.get_ident) != loop_thread


class TestCredentialFailures:
    async def test_no_credentials_at_all_is_reported_as_such(self) -> None:
        def work() -> None:
            raise NoCredentialsError

        with pytest.raises(CredentialsUnavailableError, match="PutObject"):
            await call("PutObject s3://b/k", work)

    @pytest.mark.parametrize(
        "code", ["InvalidClientTokenId", "UnrecognizedClientException", "ExpiredToken"]
    )
    async def test_a_rejected_identity_is_not_reported_as_a_bad_request(
        self, code: str
    ) -> None:
        """The distinction that matters when a short-lived session has quietly lapsed.

        Retrying the request is pointless; renewing the credential is the fix, and the error
        class is what says which of the two to do.
        """

        def work() -> None:
            raise client_error(code, "GetObject")

        with pytest.raises(CredentialsUnavailableError, match=code):
            await call("GetObject s3://b/k", work)


class TestServiceFailures:
    async def test_a_refusal_carries_the_code_and_the_operation(self) -> None:
        def work() -> None:
            raise client_error("AccessDenied", "PutObject")

        with pytest.raises(AwsRequestError, match="AccessDenied") as raised:
            await call("PutObject s3://b/k", work)
        assert "PutObject s3://b/k" in str(raised.value)

    async def test_a_refusal_naming_no_code_still_translates(self) -> None:
        """A response without an `Error` block is malformed, not a reason to leak the SDK."""

        def work() -> None:
            raise client_error(None, "PutObject")

        with pytest.raises(AwsRequestError):
            await call("PutObject s3://b/k", work)


class TestTransportFailures:
    @pytest.mark.parametrize(
        "failure",
        [
            EndpointConnectionError(endpoint_url="https://s3.example"),
            ConnectTimeoutError(endpoint_url="https://s3.example"),
        ],
    )
    async def test_no_response_at_all_becomes_a_request_error(
        self, failure: Exception
    ) -> None:
        """`BotoCoreError` and `ClientError` are siblings, so both clauses have to exist.

        Deleting either one silently stops translating half the failures, and the half that
        stops is invisible until something is actually unreachable.
        """

        def work() -> None:
            raise failure

        with pytest.raises(AwsRequestError):
            await call("GetObject s3://b/k", work)


class TestPassthrough:
    async def test_a_dexter_error_raised_by_the_work_is_left_alone(self) -> None:
        """How `get_object` reports a missing key: it raises inside `work` and is not rewrapped."""

        class MarkerError(AwsError):
            pass

        def work() -> None:
            raise MarkerError("the object is not there")

        with pytest.raises(MarkerError):
            await call("GetObject s3://b/k", work)


class TestErrorCode:
    def test_reads_the_code_out_of_the_response(self) -> None:
        assert error_code(client_error("NoSuchKey", "GetObject")) == "NoSuchKey"

    def test_answers_an_empty_string_when_none_was_given(self) -> None:
        assert error_code(client_error(None, "GetObject")) == ""
