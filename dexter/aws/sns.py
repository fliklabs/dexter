"""Publishing to a topic, and sending a text message.

Two operations that share an API and nothing else. `publish` fans a message out to whatever is
subscribed to a topic; `send_sms` reaches one phone. They are separate methods rather than one
with a union of arguments, because a topic ARN and a phone number are never both meaningful and a
single method would have to say so in prose.

**`send_sms` defaults to transactional, and that is a correction rather than a preference.** SNS
takes the SMS type from an account-level default when the caller sets none, and that default is
`Promotional` unless somebody changed it. A promotional message is cheaper, deprioritised, and
subject to carrier filtering a one-time code does not survive — so a login that works in
development and silently fails for some users in production is the shape of the bug. dexter
ships `dexter.iam`, whose magic codes are exactly this traffic, so the safe default is the
right one here.
"""

from collections.abc import Mapping
from typing import Any

from ._calling import call
from .errors import MessageTooLargeError
from .models import SmsType
from .session import AwsSession

MAX_MESSAGE_BYTES = 262144
"""256 KiB, the largest message SNS accepts.

Checked here because the service's own answer is `InvalidParameterValue`, which for a caller
publishing a serialised document says nothing about which part was too big.
"""


class SnsClient:
    """Publishes to topics and sends text messages."""

    __slots__ = ("_session",)

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients."""
        self._session = session

    async def publish(  # noqa: PLR0913 - attributes and the two FIFO ids are independent
        self,
        topic_arn: str,
        message: str,
        *,
        subject: str | None = None,
        attributes: Mapping[str, str] | None = None,
        group_id: str | None = None,
        deduplication_id: str | None = None,
    ) -> str:
        """Publish `message` to a topic and return the identifier SNS gave it.

        Args:
            topic_arn: The topic's ARN.
            message: The body. Serialising a structure into it is the caller's business —
                `json.dumps` is one line, and a framework that owned the serialisation would be
                a framework every consumer had to work around.
            subject: The subject, used by email subscriptions and ignored by the rest.
            attributes: Named strings travelling beside the message. **This is what subscription
                filter policies match on**, so it is how one topic feeds several queues that each
                want a different subset. String-valued only: the `{"DataType": "String",
                "StringValue": ...}` envelope is built here.
            group_id: The FIFO ordering group, for a FIFO topic.
            deduplication_id: The FIFO deduplication key, for a FIFO topic.

        Returns:
            SNS's message identifier.

        Raises:
            MessageTooLargeError: If the message exceeds 256 KiB.
            ResourceNotFoundError: If the topic does not exist.
            AwsRequestError: If the publish was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        _check_size(message)

        request: dict[str, Any] = {"TopicArn": topic_arn, "Message": message}
        if subject is not None:
            request["Subject"] = subject
        if attributes:
            request["MessageAttributes"] = _attributes(attributes)
        if group_id is not None:
            request["MessageGroupId"] = group_id
        if deduplication_id is not None:
            request["MessageDeduplicationId"] = deduplication_id

        response = await call(
            f"Publish to {topic_arn}",
            lambda: self._session.sns.publish(**request),
        )
        return response["MessageId"]

    async def send_sms(
        self,
        phone_number: str,
        message: str,
        *,
        sender_id: str | None = None,
        sms_type: SmsType = SmsType.TRANSACTIONAL,
    ) -> str:
        """Send `message` to one phone number and return the identifier SNS gave it.

        Args:
            phone_number: In E.164 form, such as `+61400000000`. SNS refuses anything else, and
                the refusal names the parameter rather than the format.
            message: The text.
            sender_id: The alphabetic sender the recipient sees, where the destination country
                supports one. Ignored elsewhere, which is why it is not validated here.
            sms_type: Whether the carrier should treat this as transactional or promotional.
                Defaults to transactional — see the module docstring.

        Returns:
            SNS's message identifier.

        Raises:
            MessageTooLargeError: If the message exceeds 256 KiB.
            AwsRequestError: If the send was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        _check_size(message)

        attributes: dict[str, Any] = {
            "AWS.SNS.SMS.SMSType": {
                "DataType": "String",
                "StringValue": sms_type.value.title(),
            }
        }
        if sender_id is not None:
            attributes["AWS.SNS.SMS.SenderID"] = {
                "DataType": "String",
                "StringValue": sender_id,
            }

        response = await call(
            f"Publish SMS to {phone_number}",
            lambda: self._session.sns.publish(
                PhoneNumber=phone_number,
                Message=message,
                MessageAttributes=attributes,
            ),
        )
        return response["MessageId"]


def _attributes(attributes: Mapping[str, str], /) -> dict[str, Any]:
    """Wrap plain names and values in the envelope SNS wants."""
    return {
        name: {"DataType": "String", "StringValue": value}
        for name, value in attributes.items()
    }


def _check_size(message: str, /) -> None:
    """Refuse an oversized message before it is sent.

    Measured in encoded bytes rather than characters, because that is what the limit is against
    and the two differ for any message that is not plain ASCII.
    """
    size = len(message.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The message is {size} bytes, and SNS accepts at most {MAX_MESSAGE_BYTES}."
        )
