"""The contracts: what a message is, and what can send one.

A `Protocol` for the notifier, so nothing has to inherit from dexter to be one, and frozen
pydantic models for the message, because a message crosses from a consumer's own code into
dexter and is built once — exactly the split AGENTS.md draws.

**Every collection field is a tuple.** A frozen pydantic model is only shallowly frozen, so a
`list` field would leave the model mutable and silently unhashable. That matters more than it
looks: an `Email` is the kind of value that ends up in a set of things to retry.

Nothing here knows a provider exists. `dexter.notification.resend` is one implementation of
`EmailNotifier` and there is deliberately no registry of them — a second engine is a second
`use_*` function, not an entry in a table.
"""

from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class EmailBodyType(StrEnum):
    """How the body of a message should be read."""

    TEXT = "TEXT"
    """Plain text. Rendered by the client as-is, and never interpreted as markup."""

    HTML = "HTML"
    """A complete HTML document or fragment."""


def describe_body_type(body_type: EmailBodyType, /) -> str:
    """Render a body type as the symbol a caller would type.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence. In a message aimed at
    a developer the qualified symbol is more useful: it is what they have to write.
    """
    return f"EmailBodyType.{body_type.name}"


class EmailBody(BaseModel):
    """The content of a message, and how to read it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EmailBodyType
    data: str

    @classmethod
    def text(cls, data: str, /) -> Self:
        """A plain-text body."""
        return cls(type=EmailBodyType.TEXT, data=data)

    @classmethod
    def html(cls, data: str, /) -> Self:
        """An HTML body."""
        return cls(type=EmailBodyType.HTML, data=data)


class Email(BaseModel):
    """One message, addressed and ready to send.

    dexter renders nothing. The subject and body arrive already composed, because templating
    is a choice a consumer has usually already made — and a framework that insists on its own
    engine is one they have to work around.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_address: str
    to_addresses: tuple[str, ...]
    subject: str
    body: EmailBody
    cc_addresses: tuple[str, ...] = ()
    bcc_addresses: tuple[str, ...] = ()
    reply_to: str | None = None

    @field_validator("from_address")
    @classmethod
    def _check_sender(cls, address: str) -> str:
        """Reject a blank sender, which every provider refuses anyway."""
        if not address.strip():
            raise ValueError("from_address must name a sender.")
        return address

    @model_validator(mode="after")
    def _check_recipients(self) -> Self:
        """Reject a message nobody would receive.

        Checked here rather than at send time so the traceback points at the line that built
        the message, which is where the mistake is.
        """
        if not self.to_addresses:
            raise ValueError("to_addresses must name at least one recipient.")
        return self


class EmailNotifier(Protocol):
    """Sends one message, over whatever provider it is backed by.

    Asynchronous because sending is I/O. dexter is async-native: a synchronous notifier would
    either block the event loop or need a wrapper, and neither belongs in the library.
    """

    async def send(self, email: Email) -> str:
        """Send `email` and return the provider's identifier for it.

        Raises:
            DeliveryError: If the provider refused the message or could not be reached.
        """
        ...
