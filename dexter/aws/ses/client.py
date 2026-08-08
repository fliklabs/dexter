"""Sending one message at a time.

The client is thin on purpose: `_request.py` assembles the request and holds the guards, and this
file is the send, the failure translation, and nothing else.
"""

from collections.abc import Mapping, Sequence

from botocore.exceptions import ClientError

from .._calling import call, error_code
from ..errors import EmailRejectedError
from ..session import AwsSession
from ._request import build

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

    async def send_email(  # noqa: PLR0913 - a message has this many parts; see the package docstring
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
            ValueError: If `to` is empty, or if neither `text` nor `html` was given.
            EmailRejectedError: If SES refused the message on the account's configuration.
            AwsRequestError: If the send was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        request = build(
            sender=sender,
            to=to,
            subject=subject,
            text=text,
            html=html,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            configuration_set=configuration_set,
            tags=tags,
        )

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
