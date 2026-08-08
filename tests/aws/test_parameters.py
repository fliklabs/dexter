"""Covers `dexter.aws.parameters`: reading, caching, and decrypting.

The decryption tests are the ones worth reading. Without `WithDecryption` a `SecureString` reads
back as ciphertext — a string that looks like a value and is not — so the expected parameters
given to the stubber are the assertion, not decoration: the stub refuses the call if the flag is
absent or different.
"""

import pytest
from botocore.stub import Stubber

from dexter.aws import (
    AccessDeniedError,
    AwsConfig,
    AwsSession,
    ParameterNotFoundError,
    ParameterStoreClient,
    ParameterValue,
)

from .conftest import REGION

NAME = "/app/production/orders-table"
VALUE = "orders-production"


def add_parameter(
    stub: Stubber, /, *, name: str = NAME, value: str = VALUE, decrypt: bool = True
) -> None:
    """Queue one `GetParameter`."""
    stub.add_response(
        "get_parameter",
        {"Parameter": {"Name": name, "Value": value}},
        {"Name": name, "WithDecryption": decrypt},
    )


class TestReading:
    async def test_returns_the_value(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        add_parameter(ssm_stub)
        assert await ParameterStoreClient(session).get_parameter(NAME) == VALUE

    async def test_asks_for_decryption_by_default(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """**The correction this client exists to make.**

        Reading a `SecureString` without the flag succeeds and returns ciphertext. The expected
        parameters are what make this an assertion: the stubber refuses the call if
        `WithDecryption` is absent.
        """
        add_parameter(ssm_stub, decrypt=True)
        await ParameterStoreClient(session).get_parameter(NAME)

    async def test_decryption_can_be_turned_off(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        add_parameter(ssm_stub, decrypt=False)
        await ParameterStoreClient(session).get_parameter(NAME, decrypt=False)

    async def test_a_missing_parameter_names_itself(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_client_error(
            "get_parameter",
            service_error_code="ParameterNotFound",
            http_status_code=400,
        )
        with pytest.raises(ParameterNotFoundError, match=NAME):
            await ParameterStoreClient(session).get_parameter(NAME)

    async def test_a_denial_is_not_reported_as_a_missing_parameter(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """A role without `kms:Decrypt` is a policy problem, and reading it as "not there" is
        how a misconfigured deployment looks like an unprovisioned one."""
        ssm_stub.add_client_error(
            "get_parameter",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        with pytest.raises(AccessDeniedError):
            await ParameterStoreClient(session).get_parameter(NAME)


class TestCaching:
    async def test_a_second_read_does_not_fetch_again(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """One queued response, two reads. A second fetch would raise `StubAssertionError`."""
        add_parameter(ssm_stub)
        client = ParameterStoreClient(session)

        assert await client.get_parameter(NAME) == VALUE
        assert await client.get_parameter(NAME) == VALUE

    async def test_the_decryption_flag_is_part_of_the_cache_key(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """**Leaving it out would be a real bug**, not a missed optimisation.

        The same name answers a different string under each flag — the plaintext and the
        ciphertext — so one shared key lets an undecrypted read poison every decrypted one.
        """
        add_parameter(ssm_stub, value="plaintext", decrypt=True)
        add_parameter(ssm_stub, value="AQICAHc=", decrypt=False)
        client = ParameterStoreClient(session)

        assert await client.get_parameter(NAME) == "plaintext"
        assert await client.get_parameter(NAME, decrypt=False) == "AQICAHc="

    async def test_invalidating_forgets_both_spellings_of_one_name(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """A caller holding a name does not think of the two flags as two things."""
        add_parameter(ssm_stub, value="first")
        add_parameter(ssm_stub, value="second")
        client = ParameterStoreClient(session)

        assert await client.get_parameter(NAME) == "first"
        client.invalidate(NAME)
        assert await client.get_parameter(NAME) == "second"

    async def test_invalidating_everything_forgets_every_name(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        add_parameter(ssm_stub, value="first")
        add_parameter(ssm_stub, value="second")
        client = ParameterStoreClient(session)

        await client.get_parameter(NAME)
        client.invalidate()
        assert await client.get_parameter(NAME) == "second"

    async def test_a_failed_read_is_not_cached(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_client_error(
            "get_parameter",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        add_parameter(ssm_stub)
        client = ParameterStoreClient(session)

        with pytest.raises(AccessDeniedError):
            await client.get_parameter(NAME)
        assert await client.get_parameter(NAME) == VALUE


class TestReadingMany:
    async def test_returns_every_named_value(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_response(
            "get_parameters",
            {
                "Parameters": [
                    {"Name": "/a", "Value": "one"},
                    {"Name": "/b", "Value": "two"},
                ],
            },
            {"Names": ["/a", "/b"], "WithDecryption": True},
        )
        assert await ParameterStoreClient(session).get_parameters(["/a", "/b"]) == {
            "/a": "one",
            "/b": "two",
        }

    async def test_asks_only_for_the_names_it_does_not_already_hold(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """The point of reading through the cache rather than around it."""
        add_parameter(ssm_stub, name="/a", value="one")
        ssm_stub.add_response(
            "get_parameters",
            {"Parameters": [{"Name": "/b", "Value": "two"}]},
            {"Names": ["/b"], "WithDecryption": True},
        )
        client = ParameterStoreClient(session)

        await client.get_parameter("/a")
        assert await client.get_parameters(["/a", "/b"]) == {"/a": "one", "/b": "two"}

    async def test_chunks_at_the_service_limit_of_ten(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """Eleven names is two requests. Sending eleven would be refused outright."""
        names = [f"/p{index}" for index in range(11)]
        ssm_stub.add_response(
            "get_parameters",
            {
                "Parameters": [{"Name": name, "Value": name} for name in names[:10]],
            },
            {"Names": names[:10], "WithDecryption": True},
        )
        ssm_stub.add_response(
            "get_parameters",
            {
                "Parameters": [{"Name": names[10], "Value": names[10]}],
            },
            {"Names": names[10:], "WithDecryption": True},
        )

        assert len(await ParameterStoreClient(session).get_parameters(names)) == 11

    async def test_a_missing_name_is_reported_even_though_the_call_succeeded(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """`GetParameters` answers 200 and lists what it did not recognise, so a client that
        checks only the status code silently returns a short dictionary."""
        ssm_stub.add_response(
            "get_parameters",
            {
                "Parameters": [{"Name": "/a", "Value": "one"}],
                "InvalidParameters": ["/b"],
            },
            {"Names": ["/a", "/b"], "WithDecryption": True},
        )
        with pytest.raises(ParameterNotFoundError, match="/b"):
            await ParameterStoreClient(session).get_parameters(["/a", "/b"])

    async def test_values_from_a_batch_are_cached_individually(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_response(
            "get_parameters",
            {"Parameters": [{"Name": "/a", "Value": "one"}]},
            {"Names": ["/a"], "WithDecryption": True},
        )
        client = ParameterStoreClient(session)

        await client.get_parameters(["/a"])
        assert await client.get_parameter("/a") == "one"


class TestReadingByPath:
    async def test_returns_everything_under_the_path(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": [{"Name": "/app/a", "Value": "one"}]},
            {"Path": "/app", "Recursive": True, "WithDecryption": True},
        )
        assert await ParameterStoreClient(session).get_parameters_by_path("/app") == {
            "/app/a": "one"
        }

    async def test_follows_every_page(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """**The regression test for silent truncation.**

        The service answers ten at a time. A flat read would return the first page and look
        exactly like a complete answer, which is the failure mode worth a named test.
        """
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": [{"Name": "/app/a", "Value": "one"}], "NextToken": "more"},
            {"Path": "/app", "Recursive": True, "WithDecryption": True},
        )
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": [{"Name": "/app/b", "Value": "two"}]},
            {
                "Path": "/app",
                "Recursive": True,
                "WithDecryption": True,
                "NextToken": "more",
            },
        )

        assert await ParameterStoreClient(session).get_parameters_by_path("/app") == {
            "/app/a": "one",
            "/app/b": "two",
        }

    async def test_an_empty_page_with_a_token_is_not_the_end(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """Stopping on an empty page rather than on a missing token is the classic mistake."""
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": [], "NextToken": "more"},
            {"Path": "/app", "Recursive": True, "WithDecryption": True},
        )
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": [{"Name": "/app/b", "Value": "two"}]},
            {
                "Path": "/app",
                "Recursive": True,
                "WithDecryption": True,
                "NextToken": "more",
            },
        )

        assert await ParameterStoreClient(session).get_parameters_by_path("/app") == {
            "/app/b": "two"
        }

    async def test_can_be_asked_not_to_recurse(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_response(
            "get_parameters_by_path",
            {"Parameters": []},
            {"Path": "/app", "Recursive": False, "WithDecryption": True},
        )
        await ParameterStoreClient(session).get_parameters_by_path(
            "/app", recursive=False
        )


class TestParameterValue:
    async def test_resolves_the_named_parameter(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        add_parameter(ssm_stub)
        value = ParameterValue(ParameterStoreClient(session), NAME)
        assert await value.value() == VALUE

    async def test_two_values_over_one_parameter_fetch_once(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        """What makes it right for a component to hold one of these and read it per call."""
        add_parameter(ssm_stub)
        client = ParameterStoreClient(session)

        assert await ParameterValue(client, NAME).value() == VALUE
        assert await ParameterValue(client, NAME).value() == VALUE

    async def test_carries_its_decryption_choice(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        add_parameter(ssm_stub, decrypt=False)
        value = ParameterValue(ParameterStoreClient(session), NAME, decrypt=False)
        assert await value.value() == VALUE

    def test_repr_names_the_location_and_not_a_value(self, session: AwsSession) -> None:
        value = ParameterValue(ParameterStoreClient(session), NAME)
        assert repr(value) == f"ParameterValue({NAME!r})"

    async def test_a_missing_parameter_names_it(
        self, session: AwsSession, ssm_stub: Stubber
    ) -> None:
        ssm_stub.add_client_error(
            "get_parameter",
            service_error_code="ParameterNotFound",
            http_status_code=400,
        )
        value = ParameterValue(ParameterStoreClient(session), NAME)
        with pytest.raises(ParameterNotFoundError, match=NAME):
            await value.value()


class TestTheCacheLifetime:
    async def test_a_zero_lifetime_refetches_every_time(
        self, ssm_stub: Stubber, session: AwsSession
    ) -> None:
        expiring = AwsSession(AwsConfig(region=REGION, parameter_cache_seconds=0.0))
        with Stubber(expiring.ssm) as stub:
            add_parameter(stub)
            add_parameter(stub)
            client = ParameterStoreClient(expiring)

            await client.get_parameter(NAME)
            await client.get_parameter(NAME)

            stub.assert_no_pending_responses()
