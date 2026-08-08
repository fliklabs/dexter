"""Packaging a message for SNS: attribute envelopes, and the size limit.

The two things `publish` and `send_sms` share, and the only two. Keeping them here is what lets
`client.py` be the operations and nothing else — and it is where the SMS type is turned into the
spelling the service expects, which is the one conversion in this package that is easy to get
subtly wrong.
"""

from collections.abc import Mapping
from typing import Any

from ..errors import MessageTooLargeError
from ..models import SmsType

MAX_MESSAGE_BYTES = 262144
"""256 KiB, the largest message SNS accepts.

Checked locally because the service's own answer is `InvalidParameterValue`, which for a caller
publishing a serialised document says nothing about which part was too big.
"""

SMS_TYPES: dict[SmsType, str] = {
    SmsType.TRANSACTIONAL: "Transactional",
    SmsType.PROMOTIONAL: "Promotional",
}
"""dexter's spelling of an SMS type, and the service's.

A table rather than `sms_type.value.title()`, so that a member added to `SmsType` without a
translation is a `KeyError` here rather than a string the service quietly ignores — which would
mean falling back to the account default, and that default is `Promotional`.
"""


def string(value: str, /) -> dict[str, str]:
    """One attribute in the envelope SNS wants."""
    return {"DataType": "String", "StringValue": value}


def attributes(named: Mapping[str, str], /) -> dict[str, Any]:
    """Plain names and values, wrapped one by one."""
    return {name: string(value) for name, value in named.items()}


def sms_attributes(sms_type: SmsType, sender_id: str | None, /) -> dict[str, Any]:
    """The attributes that carry an SMS's delivery class and sender."""
    envelope: dict[str, Any] = {"AWS.SNS.SMS.SMSType": string(SMS_TYPES[sms_type])}
    if sender_id is not None:
        envelope["AWS.SNS.SMS.SenderID"] = string(sender_id)
    return envelope


def check_size(message: str, /) -> None:
    """Refuse an oversized message before it is sent.

    Measured in encoded bytes rather than characters, because that is what the limit is against
    and the two differ for any message that is not plain ASCII.

    Raises:
        MessageTooLargeError: If the message exceeds 256 KiB.
    """
    size = len(message.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"The message is {size} bytes, and SNS accepts at most {MAX_MESSAGE_BYTES}."
        )
