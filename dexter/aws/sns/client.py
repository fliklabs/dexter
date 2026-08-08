"""Publishing to a topic, and sending a text message.

Two operations that share an API and nothing else. They are separate methods rather than one with
a union of arguments, because a topic ARN and a phone number are never both meaningful and a
single method would have to say so in prose.
"""

from collections.abc import Mapping
from typing import Any

from .._calling import call
from ..models import SmsType
from ..session import AwsSession
from ._envelopes import attributes as wrap_attributes
from ._envelopes import check_size, sms_attributes


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
                want a different subset. String-valued only: the envelope is built here.
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
        check_size(message)

        request: dict[str, Any] = {"TopicArn": topic_arn, "Message": message}
        if subject is not None:
            request["Subject"] = subject
        if attributes:
            request["MessageAttributes"] = wrap_attributes(attributes)
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
                Defaults to transactional — see the package docstring.

        Returns:
            SNS's message identifier.

        Raises:
            MessageTooLargeError: If the message exceeds 256 KiB.
            AwsRequestError: If the send was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        check_size(message)

        response = await call(
            f"Publish SMS to {phone_number}",
            lambda: self._session.sns.publish(
                PhoneNumber=phone_number,
                Message=message,
                MessageAttributes=sms_attributes(sms_type, sender_id),
            ),
        )
        return response["MessageId"]
