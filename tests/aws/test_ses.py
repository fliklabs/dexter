"""Covers `dexter.aws.ses`: the v2 request shape, and the guards that run before it.

The expected-parameters argument to the stubber is the real assertion in most of these. SES v2
nests content three levels deep — `Content.Simple.Body.Html.Data` — and getting that wrong is the
easy mistake the API's shape invites, so every test says exactly what the request must look like.
"""

import pytest
from botocore.stub import Stubber

from dexter.aws import AwsRequestError, AwsSession, EmailRejectedError, SesClient

SENDER = "orders@example.com"
RECIPIENT = "buyer@example.com"
SUBJECT = "Your order"
MESSAGE_ID = "0100018f-0000-0000-0000-000000000000"


def simple(**body: dict[str, str]) -> dict[str, object]:
    """The `Content` block for a simple message with the given body parts."""
    return {"Simple": {"Subject": {"Data": SUBJECT}, "Body": body}}


class TestSending:
    async def test_sends_a_plain_text_message(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": [],
                    "BccAddresses": [],
                },
                "Content": simple(Text={"Data": "hello"}),
            },
        )
        identifier = await SesClient(session).send_email(
            sender=SENDER, to=[RECIPIENT], subject=SUBJECT, text="hello"
        )
        assert identifier == MESSAGE_ID

    async def test_sends_an_html_message(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": [],
                    "BccAddresses": [],
                },
                "Content": simple(Html={"Data": "<p>hello</p>"}),
            },
        )
        await SesClient(session).send_email(
            sender=SENDER, to=[RECIPIENT], subject=SUBJECT, html="<p>hello</p>"
        )

    async def test_sends_both_bodies_as_one_message(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """**The case v1 makes awkward and v2 makes ordinary.**

        Both parts in one `Body` is a multipart alternative: the recipient's client picks. An
        HTML-only send is the one that arrives blank in a text-only reader.
        """
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": [],
                    "BccAddresses": [],
                },
                "Content": simple(
                    Text={"Data": "hello"}, Html={"Data": "<p>hello</p>"}
                ),
            },
        )
        await SesClient(session).send_email(
            sender=SENDER,
            to=[RECIPIENT],
            subject=SUBJECT,
            text="hello",
            html="<p>hello</p>",
        )

    async def test_carries_copies_and_reply_addresses(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": ["cc@example.com"],
                    "BccAddresses": ["bcc@example.com"],
                },
                "Content": simple(Text={"Data": "hello"}),
                "ReplyToAddresses": ["replies@example.com"],
            },
        )
        await SesClient(session).send_email(
            sender=SENDER,
            to=[RECIPIENT],
            subject=SUBJECT,
            text="hello",
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            reply_to=["replies@example.com"],
        )

    async def test_carries_a_configuration_set_and_tags(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """How bounces and complaints reach an event destination without a second call."""
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": [],
                    "BccAddresses": [],
                },
                "Content": simple(Text={"Data": "hello"}),
                "ConfigurationSetName": "transactional",
                "EmailTags": [{"Name": "kind", "Value": "receipt"}],
            },
        )
        await SesClient(session).send_email(
            sender=SENDER,
            to=[RECIPIENT],
            subject=SUBJECT,
            text="hello",
            configuration_set="transactional",
            tags={"kind": "receipt"},
        )

    async def test_omits_optional_blocks_when_nothing_was_given(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        """An empty `ReplyToAddresses` is not the same as an absent one to the service, and the
        stubber fails the call if a key it was not told about appears."""
        ses_stub.add_response(
            "send_email",
            {"MessageId": MESSAGE_ID},
            {
                "FromEmailAddress": SENDER,
                "Destination": {
                    "ToAddresses": [RECIPIENT],
                    "CcAddresses": [],
                    "BccAddresses": [],
                },
                "Content": simple(Text={"Data": "hello"}),
            },
        )
        await SesClient(session).send_email(
            sender=SENDER, to=[RECIPIENT], subject=SUBJECT, text="hello"
        )


class TestGuard:
    """Both guards run before any request, which is why no stub is installed here: a call that
    reached the service would fail as something other than a `ValueError`."""

    async def test_rejects_a_message_with_no_recipients(
        self, session: AwsSession
    ) -> None:
        """Raised at the call, so the traceback points at the line that built the message."""
        with pytest.raises(ValueError, match="recipient"):
            await SesClient(session).send_email(
                sender=SENDER, to=[], subject=SUBJECT, text="hello"
            )

    async def test_rejects_a_message_with_no_body(self, session: AwsSession) -> None:
        with pytest.raises(ValueError, match="body"):
            await SesClient(session).send_email(
                sender=SENDER, to=[RECIPIENT], subject=SUBJECT
            )


class TestRefusal:
    @pytest.mark.parametrize(
        "code",
        [
            "MessageRejected",
            "MailFromDomainNotVerifiedException",
            "AccountSuspendedException",
            "SendingPausedException",
        ],
    )
    async def test_an_account_problem_is_its_own_failure(
        self, session: AwsSession, ses_stub: Stubber, code: str
    ) -> None:
        """These will refuse the same message again until SES itself is changed, which is what
        separates them from a request that was merely wrong."""
        ses_stub.add_client_error(
            "send_email", service_error_code=code, http_status_code=400
        )
        with pytest.raises(EmailRejectedError, match=RECIPIENT):
            await SesClient(session).send_email(
                sender=SENDER, to=[RECIPIENT], subject=SUBJECT, text="hello"
            )

    async def test_anything_else_stays_a_request_error(
        self, session: AwsSession, ses_stub: Stubber
    ) -> None:
        ses_stub.add_client_error(
            "send_email", service_error_code="BadRequestException", http_status_code=400
        )
        with pytest.raises(AwsRequestError):
            await SesClient(session).send_email(
                sender=SENDER, to=[RECIPIENT], subject=SUBJECT, text="hello"
            )
