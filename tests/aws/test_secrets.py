"""Covers `dexter.aws.secrets`: parsing, the cache, and the credential over it.

The cache tests are the ones that matter. An uncached implementation passes every other test in
this file and then costs a Secrets Manager round-trip per model call in production — which is
exactly the state of the library this module was written to improve on.

Most "how many times did it fetch" assertions are made by the stubber rather than by a counter:
one queued response and two calls means the second fetch raises, and `assert_no_pending_responses`
catches the reverse.

**The single-flight test is the exception, and had to be.** The stubber cannot express it: the
version of that test which merely gathers ten reads passes with the lock deleted, because the
first read completes before the rest are scheduled and they all hit the plain cache check. It
holds the fetch open instead — see the test.
"""

import asyncio
import json
import threading

import pytest
from botocore.stub import Stubber

from dexter.aws import (
    AwsConfig,
    AwsRequestError,
    AwsSession,
    SecretNotFoundError,
    SecretsManagerClient,
    SecretValue,
)

from .conftest import REGION, SECRET_ID
from .helpers import client_error

VALUES = {
    "OPENAI_API_KEY": "sk-openai",
    "GEMINI_API_KEY": "sk-gemini",
    "ALIBABACLOUD_MODELSTUDIO_API_KEY": "sk-qwen",
}


def add_secret(stub: Stubber, /, *, document: str | None = None) -> None:
    """Queue one `GetSecretValue` answering with `document`, or the standard one."""
    stub.add_response(
        "get_secret_value",
        {"SecretString": json.dumps(VALUES) if document is None else document},
        {"SecretId": SECRET_ID},
    )


class TestReading:
    async def test_returns_the_parsed_document(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        assert await SecretsManagerClient(session).get_secret(SECRET_ID) == VALUES

    async def test_returns_one_named_value(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)
        assert await client.get_secret_key(SECRET_ID, "GEMINI_API_KEY") == "sk-gemini"

    async def test_reports_a_refusal_as_a_request_error(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        secrets_stub.add_client_error(
            "get_secret_value", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(AwsRequestError):
            await SecretsManagerClient(session).get_secret(SECRET_ID)


class TestGuard:
    async def test_rejects_a_secret_that_is_not_json(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub, document="sk-not-json")
        with pytest.raises(SecretNotFoundError, match="not valid JSON"):
            await SecretsManagerClient(session).get_secret(SECRET_ID)

    async def test_rejects_a_secret_that_is_json_but_not_an_object(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """A bare string is valid JSON, so this is not caught by the parse."""
        add_secret(secrets_stub, document='"sk-a-bare-string"')
        with pytest.raises(SecretNotFoundError, match="rather than a JSON object"):
            await SecretsManagerClient(session).get_secret(SECRET_ID)

    async def test_rejects_a_binary_secret(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        secrets_stub.add_response(
            "get_secret_value", {"SecretBinary": b"\x00\x01"}, {"SecretId": SECRET_ID}
        )
        with pytest.raises(SecretNotFoundError, match="binary"):
            await SecretsManagerClient(session).get_secret(SECRET_ID)

    async def test_rejects_a_missing_key(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)
        with pytest.raises(SecretNotFoundError, match="OXYLABS_USER_API_KEY"):
            await client.get_secret_key(SECRET_ID, "OXYLABS_USER_API_KEY")

    async def test_does_not_list_the_other_key_names_when_one_is_missing(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """This message reaches a log, and an inventory of a secret store does not belong there."""
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)
        with pytest.raises(SecretNotFoundError) as raised:
            await client.get_secret_key(SECRET_ID, "ABSENT")
        assert "OPENAI_API_KEY" not in str(raised.value)

    async def test_rejects_a_value_that_is_not_a_string(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub, document=json.dumps({"PORT": 5432}))
        client = SecretsManagerClient(session)
        with pytest.raises(SecretNotFoundError, match="rather than a string"):
            await client.get_secret_key(SECRET_ID, "PORT")


class TestCaching:
    async def test_a_second_read_does_not_fetch_again(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """One queued response, two reads. A second fetch would raise `StubAssertionError`."""
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)

        assert await client.get_secret(SECRET_ID) == VALUES
        assert await client.get_secret(SECRET_ID) == VALUES

    async def test_every_key_of_one_secret_shares_a_single_fetch(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """The case this exists for: three provider keys, one document, one request."""
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)

        for key, expected in VALUES.items():
            assert await client.get_secret_key(SECRET_ID, key) == expected

    async def test_refetches_once_the_entry_has_expired(
        self, secrets_stub: Stubber, session: AwsSession
    ) -> None:
        """A zero lifetime expires immediately, so expiry is tested without waiting for it."""
        expiring = AwsSession(AwsConfig(region=REGION, secret_cache_seconds=0.0))
        with Stubber(expiring.secrets) as stub:
            add_secret(stub)
            add_secret(stub)
            client = SecretsManagerClient(expiring)

            await client.get_secret(SECRET_ID)
            await client.get_secret(SECRET_ID)

            stub.assert_no_pending_responses()

    async def test_ten_callers_arriving_together_fetch_once(
        self, session: AwsSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single-flight, asserted against genuine overlap.

        **Gathering ten reads is not enough to test this**, and the obvious version of this test
        is worse than useless: the first caller finishes before the rest are scheduled, so they
        all hit the plain cache check at the top of `get_secret` and never reach the lock at all.
        That version passes with the lock deleted.

        So the fetch is held open. The first caller blocks inside the client until `release` is
        set, which guarantees the other nine get as far as the lock with the cache still empty —
        the exact state the lock exists for. Without it, all ten would be inside the fake at once
        and `calls` would read ten.
        """
        started = threading.Event()
        release = threading.Event()
        calls = 0

        def held_open(**_: object) -> dict[str, str]:
            nonlocal calls
            calls += 1
            started.set()
            assert release.wait(timeout=5), "the test never released the fetch"
            return {"SecretString": json.dumps(VALUES)}

        monkeypatch.setattr(session.secrets, "get_secret_value", held_open)
        client = SecretsManagerClient(session)

        readers = asyncio.gather(*(client.get_secret(SECRET_ID) for _ in range(10)))
        await asyncio.to_thread(started.wait, 5)
        # Let every other reader run until it blocks. The first is parked in a worker thread
        # holding the lock, so each of the nine must queue behind it rather than fetch.
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()

        assert all(result == VALUES for result in await readers)
        assert calls == 1

    async def test_a_failed_fetch_is_not_cached(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """A transient denial must not poison the cache for the life of the process."""
        secrets_stub.add_client_error(
            "get_secret_value", service_error_code="AccessDenied", http_status_code=403
        )
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)

        with pytest.raises(AwsRequestError):
            await client.get_secret(SECRET_ID)
        assert await client.get_secret(SECRET_ID) == VALUES

    async def test_two_secrets_do_not_share_a_cache_entry(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        other = "plum/other/secrets"
        add_secret(secrets_stub)
        secrets_stub.add_response(
            "get_secret_value",
            {"SecretString": json.dumps({"OPENAI_API_KEY": "sk-other"})},
            {"SecretId": other},
        )
        client = SecretsManagerClient(session)

        assert await client.get_secret_key(SECRET_ID, "OPENAI_API_KEY") == "sk-openai"
        assert await client.get_secret_key(other, "OPENAI_API_KEY") == "sk-other"


class TestSecretValue:
    async def test_resolves_the_named_key(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        credential = SecretValue(
            SecretsManagerClient(session), SECRET_ID, "OPENAI_API_KEY"
        )
        assert await credential.value() == "sk-openai"

    async def test_two_credentials_over_one_secret_fetch_once(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """What makes it right for an engine to hold one of these and resolve it per call."""
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)
        openai = SecretValue(client, SECRET_ID, "OPENAI_API_KEY")
        gemini = SecretValue(client, SECRET_ID, "GEMINI_API_KEY")

        assert await openai.value() == "sk-openai"
        assert await gemini.value() == "sk-gemini"

    def test_repr_names_the_location_and_not_a_value(self, session: AwsSession) -> None:
        """A secret's name is a location. Printing it is what makes a misconfiguration readable."""
        credential = SecretValue(
            SecretsManagerClient(session), SECRET_ID, "OPENAI_API_KEY"
        )
        assert repr(credential) == (f"SecretValue('{SECRET_ID}', 'OPENAI_API_KEY')")

    async def test_a_missing_key_names_the_key(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        credential = SecretValue(
            SecretsManagerClient(session), SECRET_ID, "NOT_PROVISIONED"
        )
        with pytest.raises(SecretNotFoundError, match="NOT_PROVISIONED"):
            await credential.value()


class TestErrorCodeHelper:
    def test_the_shared_factory_builds_what_botocore_does(self) -> None:
        """Guards the test helper itself, which every other assertion here depends on."""
        assert "NoSuchKey" in str(client_error("NoSuchKey", "GetObject"))


class TestInvalidation:
    async def test_invalidating_one_secret_forces_a_refetch(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        """The operator path for a rotation that cannot wait for the lifetime to run out."""
        add_secret(secrets_stub)
        add_secret(secrets_stub, document=json.dumps({"OPENAI_API_KEY": "sk-rotated"}))
        client = SecretsManagerClient(session)

        assert await client.get_secret_key(SECRET_ID, "OPENAI_API_KEY") == "sk-openai"
        client.invalidate(SECRET_ID)
        assert await client.get_secret_key(SECRET_ID, "OPENAI_API_KEY") == "sk-rotated"

    async def test_invalidating_everything_forgets_every_secret(
        self, session: AwsSession, secrets_stub: Stubber
    ) -> None:
        add_secret(secrets_stub)
        add_secret(secrets_stub)
        client = SecretsManagerClient(session)

        await client.get_secret(SECRET_ID)
        client.invalidate()
        assert await client.get_secret(SECRET_ID) == VALUES
