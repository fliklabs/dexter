"""A notifier that sends nothing and remembers everything.

This is what a test binds, and what an application binds before it has a provider account. It
is shipped rather than left to each consumer for the same reason `InMemoryCatalogue` exists in
the reference application: the alternative is every project writing the same twenty lines, each
slightly differently, and each one being the thing their auth tests actually exercise.

**It is not a mock.** It implements `EmailNotifier` honestly, returns a stable identifier, and
records what it was asked to send in order — so a test asserts on the message a consumer built
rather than on a call signature.
"""

from .models import Email


class RecordingEmailNotifier:
    """An `EmailNotifier` that records messages instead of delivering them.

    Slotted rather than pydantic: it is a piece of test machinery built once per container, and
    nothing about it crosses a validation boundary.
    """

    __slots__ = ("_prefix", "sent")

    def __init__(self, prefix: str = "recorded") -> None:
        """Start with nothing sent.

        Args:
            prefix: Prepended to the identifier each send returns, so a test reading a log can
                tell a recorded identifier from a real provider's.
        """
        self._prefix = prefix
        self.sent: list[Email] = []
        """Every message passed to `send`, in order."""

    async def send(self, email: Email) -> str:
        """Record `email` and return an identifier for it."""
        self.sent.append(email)
        return f"{self._prefix}-{len(self.sent)}"

    @property
    def last(self) -> Email | None:
        """The most recent message, or `None` when nothing has been sent."""
        return self.sent[-1] if self.sent else None

    def clear(self) -> None:
        """Forget everything recorded so far."""
        self.sent.clear()

    def __repr__(self) -> str:
        return f"RecordingEmailNotifier(sent={len(self.sent)})"
