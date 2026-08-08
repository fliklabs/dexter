"""Covers `dexter.notification.ses`: the adapter, and the failures it translates.

Driven through a real `SesClient` against `botocore.stub.Stubber`, rather than a fake client.
The adapter's whole job is producing the right call, and a fake would accept any call at all.

The error tests are the substance. A consumer holding an `EmailNotifier` was promised
`DeliveryError` and has no reason to import `dexter.aws` — so an `AwsError` reaching them is the
seam leaking, not a detail.
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.stub import Stubber

from dexter.aws import AwsConfig, AwsSession, SesClient
from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
    Scope,
)
from dexter.notification import (
    DeliveryError,
    Email,
    EmailBody,
    EmailNotifier,
    use_recording_notification,
)
from dexter.notification.ses import SesEmailNotifier, use_ses_notification

REGION = "ap-southeast-2"
SENDER = "orders@example.com"
RECIPIENT = "buyer@example.com"
MESSAGE_ID = "0100018f-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give boto3 an identity that is definitely not anybody's, and no config to read.

    The same safety property as `tests/aws/conftest.py`: without it a missing stub reaches a real
    account, or hangs for the metadata timeout on a machine with none.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    )
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test-session-token")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def session() -> AwsSession:
    """A real session, with real boto3 clients that no request will leave."""
    return AwsSession(AwsConfig(region=REGION))


@pytest.fixture
def ses_stub(session: AwsSession) -> Iterator[Stubber]:
    """Intercept the SES client, and fail the test if a queued response went unused."""
    with Stubber(session.ses) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def make_email(**overrides: Any) -> Email:
    """An email with everything filled in, overridable per test."""
    return Email(
        **{
            "from_address": SENDER,
            "to_addresses": (RECIPIENT,),
            "subject": "Your order",
            "body": EmailBody.text("hello"),
            **overrides,
        }
    )


def expects(**overrides: Any) -> dict[str, Any]:
    """The request the adapter should produce for `make_email()`."""
    return {
        "FromEmailAddress": SENDER,
        "Destination": {
            "ToAddresses": [RECIPIENT],
            "CcAddresses": [],
            "BccAddresses": [],
        },
        "Content": {
            "Simple": {
                "Subject": {"Data": "Your order"},
                "Body": {"Text": {"Data": "hello"}},
            }
        },
        **overrides,
    }


class TestSending:
    async def test_sends_a_text_body_as_text(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """**Never wrapped in markup.** A `<` in a code or a subject would render as a tag, and
        the recipient would see something the sender never wrote."""
        ses_stub.add_response("send_email", {"MessageId": MESSAGE_ID}, expects())

        notifier = SesEmailNotifier(SesClient(session))
        assert await notifier.send(make_email()) == MESSAGE_ID

    async def test_sends_an_html_body_as_html(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            expects(
                Content={
                    "Simple": {
                        "Subject": {"Data": "Your order"},
                        "Body": {"Html": {"Data": "<p>hello</p>"}},
                    }
                }
            ),
        )

        notifier = SesEmailNotifier(SesClient(session))
        await notifier.send(make_email(body=EmailBody.html("<p>hello</p>")))

    async def test_carries_copies(self, session: AwsSession, ses_stub: Stubber) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            expects(
                Destination={
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": ["cc@example.com"],
                    "BccAddresses": ["bcc@example.com"],
                }
            ),
        )

        notifier = SesEmailNotifier(SesClient(session))
        await notifier.send(
            make_email(
                cc_addresses=("cc@example.com",), bcc_addresses=("bcc@example.com",)
            )
        )

    async def test_carries_a_reply_to_address(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            expects(ReplyToAddresses=["replies@example.com"]),
        )

        notifier = SesEmailNotifier(SesClient(session))
        await notifier.send(make_email(reply_to="replies@example.com"))

    async def test_no_reply_to_sends_no_reply_to(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """An empty list is not the same as an absent key, and the stubber fails the call if a
        key it was not told about appears."""
        ses_stub.add_response("send_email", {"MessageId": MESSAGE_ID}, expects())

        notifier = SesEmailNotifier(SesClient(session))
        await notifier.send(make_email(reply_to=None))


class TestFailures:
    async def test_a_refusal_becomes_a_delivery_error(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_client_error(
            "send_email", service_error_code="MessageRejected", http_status_code=400
        )
        notifier = SesEmailNotifier(SesClient(session))

        with pytest.raises(DeliveryError, match="refused"):
            await notifier.send(make_email())

    async def test_any_other_aws_failure_becomes_a_delivery_error(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """**The seam.** A caller holding an `EmailNotifier` was promised the contract's error
        and has no reason to import `dexter.aws` to handle one."""
        ses_stub.add_client_error(
            "send_email",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        notifier = SesEmailNotifier(SesClient(session))

        with pytest.raises(DeliveryError, match="could not be reached"):
            await notifier.send(make_email())

    async def test_a_throttle_becomes_a_delivery_error_too(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_client_error(
            "send_email", service_error_code="Throttling", http_status_code=400
        )
        notifier = SesEmailNotifier(SesClient(session))

        with pytest.raises(DeliveryError):
            await notifier.send(make_email())


class TestWiring:
    async def test_binds_the_notifier_under_both_keys(self) -> None:
        builder = ContainerBuilder()
        builder.register(AwsConfig).to_instance(AwsConfig(region=REGION))
        builder.register(AwsSession).to(AwsSession, scope=Scope.SINGLETON)
        builder.register(SesClient).to(SesClient, scope=Scope.SINGLETON)
        use_ses_notification(builder)

        container = builder.build()
        try:
            engine = await container.resolve(SesEmailNotifier)
            assert await container.resolve(EmailNotifier) is engine
        finally:
            await container.aclose()

    def test_two_engines_cannot_both_be_wired(self) -> None:
        """They bind the same key, and the container refusing the second is the right failure —
        the alternative is an application that sends real mail from its test suite."""
        builder = ContainerBuilder()
        use_recording_notification(builder)

        with pytest.raises(DuplicateRegistrationError):
            use_ses_notification(builder)
