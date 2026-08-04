"""Sending messages, without naming who sends them.

A notifier is an ordinary class with one async method. Code that sends mail names the
contract, never an engine::

    class SendMagicCode:
        def __init__(self, notifier: EmailNotifier) -> None:
            self.notifier = notifier

        async def handle(self, request: RequestCode) -> Sent:
            await self.notifier.send(
                Email(
                    from_address="Plum <noreply@example.com>",
                    to_addresses=(request.email,),
                    subject="Your code",
                    body=EmailBody.text(f"Your code is {code}."),
                )
            )
            return Sent()

Which engine actually sends is one line of wiring, and it is the only line that changes
between a test suite and production::

    use_recording_notification(builder)  # from here — records, sends nothing
    use_resend_notification(builder)  # from dexter.notification.resend

**`ResendEmailNotifier` lives in `dexter.notification.resend`, not here.** Everything in this
package is provider-agnostic and importing it pulls in no HTTP client; the engine is a package
of its own so that the boundary is real rather than a convention, and so that `httpx` can stay
an optional dependency a consumer opts into.

**dexter renders nothing.** A subject and a body arrive already composed. Templating is a
choice most consumers have already made, and a framework that insists on its own engine is one
they have to work around.
"""

from .errors import DeliveryError as DeliveryError
from .errors import NotificationError as NotificationError
from .models import Email as Email
from .models import EmailBody as EmailBody
from .models import EmailBodyType as EmailBodyType
from .models import EmailNotifier as EmailNotifier
from .models import describe_body_type as describe_body_type
from .recording import RecordingEmailNotifier as RecordingEmailNotifier
from .use import use_recording_notification as use_recording_notification
