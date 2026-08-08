"""Covers `dexter.aws.sns`: publishing to a topic, and sending a text message.

The SMS type test is the one that matters. SNS takes the type from an account-level default when
a caller sets none, and that default is promotional — so a one-time code sent without it is
deprioritised and carrier-filtered, and the symptom is a login that works in development and
fails for some users in production.
"""

import pytest
from botocore.stub import Stubber

from dexter.aws import AwsSession, MessageTooLargeError, SmsType, SnsClient

TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:orders"
PHONE = "+61400000000"
MESSAGE_ID = "11111111-2222-3333-4444-555555555555"


def string(value: str) -> dict[str, str]:
    """One message attribute in the envelope SNS wants."""
    return {"DataType": "String", "StringValue": value}


class TestPublishing:
    async def test_publishes_to_the_topic(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {"TopicArn": TOPIC, "Message": "hello"},
        )
        assert await SnsClient(session).publish(TOPIC, "hello") == MESSAGE_ID

    async def test_carries_a_subject(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {"TopicArn": TOPIC, "Message": "hello", "Subject": "Orders"},
        )
        await SnsClient(session).publish(TOPIC, "hello", subject="Orders")

    async def test_wraps_attributes_in_the_envelope(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        """What subscription filter policies match on, so one topic can feed several queues."""
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {
                "TopicArn": TOPIC,
                "Message": "hello",
                "MessageAttributes": {"kind": string("order.created")},
            },
        )
        await SnsClient(session).publish(
            TOPIC, "hello", attributes={"kind": "order.created"}
        )

    async def test_carries_fifo_ordering_and_deduplication(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {
                "TopicArn": TOPIC,
                "Message": "hello",
                "MessageGroupId": "customer-1",
                "MessageDeduplicationId": "order-1",
            },
        )
        await SnsClient(session).publish(
            TOPIC, "hello", group_id="customer-1", deduplication_id="order-1"
        )

    async def test_omits_every_optional_key_when_nothing_was_given(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        """The stubber fails the call if a key it was not told about appears, so this asserts
        that an unset argument sends nothing rather than an empty something."""
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {"TopicArn": TOPIC, "Message": "hello"},
        )
        await SnsClient(session).publish(TOPIC, "hello")


class TestSms:
    async def test_sends_as_transactional_by_default(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        """**The correction the reference library needs.**

        Setting nothing inherits the account default, which is promotional — cheaper,
        deprioritised, and filtered by carriers in a way a login code does not survive.
        """
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {
                "PhoneNumber": PHONE,
                "Message": "123456",
                "MessageAttributes": {"AWS.SNS.SMS.SMSType": string("Transactional")},
            },
        )
        assert await SnsClient(session).send_sms(PHONE, "123456") == MESSAGE_ID

    async def test_can_be_sent_as_promotional(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {
                "PhoneNumber": PHONE,
                "Message": "sale",
                "MessageAttributes": {"AWS.SNS.SMS.SMSType": string("Promotional")},
            },
        )
        await SnsClient(session).send_sms(PHONE, "sale", sms_type=SmsType.PROMOTIONAL)

    async def test_carries_a_sender_id(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        sns_stub.add_response(
            "publish",
            {"MessageId": MESSAGE_ID},
            {
                "PhoneNumber": PHONE,
                "Message": "123456",
                "MessageAttributes": {
                    "AWS.SNS.SMS.SMSType": string("Transactional"),
                    "AWS.SNS.SMS.SenderID": string("EXAMPLE"),
                },
            },
        )
        await SnsClient(session).send_sms(PHONE, "123456", sender_id="EXAMPLE")


class TestGuard:
    async def test_refuses_an_oversized_publish(self, session: AwsSession) -> None:
        """Checked locally, because the service's own answer names no size and no message."""
        with pytest.raises(MessageTooLargeError, match="262144"):
            await SnsClient(session).publish(TOPIC, "x" * 262145)

    async def test_refuses_an_oversized_text_message(self, session: AwsSession) -> None:
        with pytest.raises(MessageTooLargeError):
            await SnsClient(session).send_sms(PHONE, "x" * 262145)

    async def test_measures_encoded_bytes_rather_than_characters(
        self, session: AwsSession
    ) -> None:
        """The limit is on bytes, and the two differ for anything that is not plain ASCII.

        131073 emoji are 131073 characters and 524292 bytes, so a character count would let this
        through and the service would refuse it.
        """
        with pytest.raises(MessageTooLargeError):
            await SnsClient(session).publish(TOPIC, "🙂" * 131073)

    async def test_a_message_at_the_limit_is_allowed(
        self, session: AwsSession, sns_stub: Stubber
    ) -> None:
        """The boundary itself, so the guard is not off by one in the expensive direction."""
        body = "x" * 262144
        sns_stub.add_response(
            "publish", {"MessageId": MESSAGE_ID}, {"TopicArn": TOPIC, "Message": body}
        )
        await SnsClient(session).publish(TOPIC, body)
