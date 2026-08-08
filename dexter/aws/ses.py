"""Sending mail through SES.

**`sesv2`, not `ses`, and the choice is worth the paragraph.** The v1 API is in maintenance:
everything added since 2019 — configuration-set engagement metrics, the Virtual Deliverability
Manager, list and contact management, per-send tags — exists only in v2. More immediately, v2
folds Simple, Raw and Templated sending into one `Content` union on one `SendEmail`, so a
text-and-HTML message and a MIME message with an attachment are the same operation; v1 needs
three. The usual reason to stay on v1 is that migrating means changing an IAM policy, and it does
not: the action is `ses:SendEmail` for both.

What it costs is that the request shape — `Content.Simple.Body.{Text,Html}.Data` — is deeper than
v1's and easy to get wrong, and that every search result shows v1. That is a documentation cost
rather than a capability one, and `botocore.stub.Stubber` catches the mistake in a test.

**This file names no dexter module.** Plugging SES into `dexter.notification`'s `EmailNotifier`
contract is `dexter/notification/ses/`, beside the Resend engine and pointing the same way. A
worker sending a receipt through this client pulls in no notification module, and nothing here
knows one exists.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from botocore.exceptions import ClientError

from ._calling import call, error_code
from .errors import EmailRejectedError
from .session import AwsSession

REJECTION_CODES = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "AccountSuspendedException",
        "SendingPausedException",
    }
)
"""What SES says when the account, not the request, is the problem.

Each of these means the message will be refused again until somebody changes the SES
configuration — a domain verified, a suppression lifted, a pause released. Separating them from
the general request failure is what lets a caller stop retrying.
"""


class SesClient:
    """Sends one message at a time, over SES v2."""

    __slots__ = ("_session",)

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients."""
        self._session = session

    async def send_email(  # noqa: PLR0913 - a message has this many parts; see the module docstring
        self,
        *,
        sender: str,
        to: Sequence[str],
        subject: str,
        text: str | None = None,
        html: str | None = None,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        reply_to: Sequence[str] = (),
        configuration_set: str | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> str:
        """Send one message and return the identifier SES gave it.

        Args:
            sender: The `From` address. Its domain or the address itself must be a verified
                identity in this account and region.
            to: The recipients. At least one is required.
            subject: The subject line, already composed — dexter renders nothing.
            text: The plain-text body.
            html: The HTML body. **Giving both is the correct thing to do**, not a conflict: SES
                assembles a multipart alternative, and the recipient's client picks. A message
                sent as HTML alone is the one that arrives blank in a text-only reader.
            cc: Carbon-copy recipients.
            bcc: Blind carbon-copy recipients.
            reply_to: Where replies should go, when that is not `sender`.
            configuration_set: The configuration set to attribute the send to, which is how
                bounces and complaints reach an event destination without a second call.
            tags: Names and values recorded against the send, for the same event stream.

        Returns:
            SES's message identifier.

        Raises:
            ValueError: If `to` is empty, or if neither `text` nor `html` was given. Both are
                raised here rather than left to the service, so the traceback points at the call
                that made the message.
            EmailRejectedError: If SES refused the message on the account's configuration.
            AwsRequestError: If the send was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        if not to:
            raise ValueError("send_email needs at least one recipient in `to`.")
        if text is None and html is None:
            raise ValueError("send_email needs a `text` body, an `html` body, or both.")

        body: dict[str, Any] = {}
        if text is not None:
            body["Text"] = {"Data": text}
        if html is not None:
            body["Html"] = {"Data": html}

        request: dict[str, Any] = {
            "FromEmailAddress": sender,
            "Destination": {
                "ToAddresses": list(to),
                "CcAddresses": list(cc),
                "BccAddresses": list(bcc),
            },
            "Content": {"Simple": {"Subject": {"Data": subject}, "Body": body}},
        }
        if reply_to:
            request["ReplyToAddresses"] = list(reply_to)
        if configuration_set is not None:
            request["ConfigurationSetName"] = configuration_set
        if tags:
            request["EmailTags"] = [
                {"Name": name, "Value": value} for name, value in tags.items()
            ]

        def send() -> str:
            try:
                response = self._session.ses.send_email(**request)
            except ClientError as error:
                if error_code(error) in REJECTION_CODES:
                    raise EmailRejectedError(
                        f"SES refused the message to {to[0]}: {error}"
                    ) from error
                raise
            return response["MessageId"]

        return await call(f"SendEmail to {to[0]}", send)
