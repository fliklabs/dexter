"""Sending an `Email` through SES.

**The adapter is the whole file, and it is deliberately thin.** `dexter.aws.SesClient` already
speaks SES v2, translates its failures and runs off the event loop; what is missing is the shape
of the message, because `dexter.notification` owns that and `dexter.aws` must not know it exists.
So this maps one type onto one call and translates one error, and there is nothing else in it.

**The direction of the dependency is the point.** This package imports `dexter.aws`, and nothing
in `dexter.aws` imports `dexter.notification` — the same way `dexter.notification.resend` reaches
for `httpx` and the core reaches for neither. A worker sending a receipt through `SesClient`
directly pulls in no notification module at all.
"""

from dexter.aws import AwsError, EmailRejectedError, SesClient

from ..errors import DeliveryError
from ..models import Email, EmailBody, EmailBodyType


class SesEmailNotifier:
    """An `EmailNotifier` backed by SES.

    A drop-in alternative to the Resend engine: an application swaps one `use_*` call for the
    other and nothing that sends mail changes.
    """

    __slots__ = ("_client",)

    def __init__(self, client: SesClient) -> None:
        """Take the AWS client that does the sending."""
        self._client = client

    async def send(self, email: Email) -> str:
        """Send `email` and return the identifier SES gave it.

        Raises:
            DeliveryError: If SES refused the message or could not be reached. **Every AWS
                failure becomes this**, because a caller holding an `EmailNotifier` was promised
                the contract's error and has no reason to import `dexter.aws` to handle one.
        """
        text, html = _bodies(email.body)
        try:
            return await self._client.send_email(
                sender=email.from_address,
                to=email.to_addresses,
                subject=email.subject,
                text=text,
                html=html,
                cc=email.cc_addresses,
                bcc=email.bcc_addresses,
                reply_to=() if email.reply_to is None else (email.reply_to,),
            )
        except EmailRejectedError as error:
            raise DeliveryError(f"SES refused the message: {error}") from error
        except AwsError as error:
            # The whole tree, not a list of the failures worth naming. `AwsError` is the root,
            # so a class added to `dexter.aws` later is caught here without an edit — where a
            # list would let it through as a bare `AwsError` a consumer never expected.
            raise DeliveryError(f"SES could not be reached: {error}") from error


def _bodies(body: EmailBody, /) -> tuple[str | None, str | None]:
    """The plain-text and HTML halves of a body, whichever it is.

    A lookup rather than a chain of `if`s, so there is no final unreachable branch to either
    leave uncovered or excuse with a pragma — this repository has none.

    **A text body is sent as text and never wrapped in markup.** Wrapping it in `<p>` would let
    a `<` in a code or a subject render as a tag, and the recipient would see something the
    sender never wrote.
    """
    return {
        EmailBodyType.TEXT: (body.data, None),
        EmailBodyType.HTML: (None, body.data),
    }[body.type]
