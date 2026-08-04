"""Sending mail through Resend's HTTP API.

The provider's own SDK is deliberately not used. It is synchronous, and it carries its API key
in a module-level global — so two applications in one process cannot use two accounts, and the
key is set by whichever notifier happened to send first. dexter is async-native and binds
configuration per container, and both of those are lost the moment that SDK is imported.

What is used instead is one documented POST, which is the whole of Resend's send API:

    POST https://api.resend.com/emails
    Authorization: Bearer <api key>
    {"from": ..., "to": [...], "subject": ..., "html" | "text": ...}

The response carries `{"id": ...}`. Anything else — a refusal, an unreachable host, an answer
without an identifier — is a `DeliveryError` naming what came back.
"""

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from ..errors import DeliveryError
from ..models import Email, EmailBody, EmailBodyType

ENDPOINT = "/emails"
"""The send path, relative to `ResendConfig.base_url`."""

RESEND_FIELD = {EmailBodyType.TEXT: "text", EmailBodyType.HTML: "html"}
"""Which field of the provider's payload carries each kind of body."""


class ResendConfig(BaseModel):
    """What the notifier needs to reach the provider.

    Frozen and `extra="forbid"` like every dexter type built once from what a consumer wrote.
    The API key is a value the application owns — dexter reads no environment and no files, so
    where it comes from is entirely the consumer's decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str
    base_url: str = "https://api.resend.com"
    timeout_seconds: float = 10.0
    """How long to wait for the provider. A send that hangs holds a request open behind it."""

    @field_validator("api_key")
    @classmethod
    def _check_key(cls, api_key: str) -> str:
        """Reject an empty key here rather than as a 401 from the provider later."""
        if not api_key.strip():
            raise ValueError("api_key must not be empty.")
        return api_key


class ResendEmailNotifier:
    """An `EmailNotifier` backed by Resend.

    **A client per send.** dexter owns no connection pool and starts nothing in the background,
    so there is no lifetime for a shared `httpx.AsyncClient` to belong to. A consumer who wants
    pooling binds their own notifier over a client the container disposes; paying a connection
    per message is the honest default, and a magic-code email is not a hot path.
    """

    __slots__ = ("_config",)

    def __init__(self, config: ResendConfig) -> None:
        """Record how to reach the provider."""
        self._config = config

    async def send(self, email: Email) -> str:
        """Send `email` through Resend and return the identifier it hands back.

        Raises:
            DeliveryError: If the provider refused the message, could not be reached, or
                answered without an identifier.
        """
        payload = self._payload(email)
        try:
            async with httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
            ) as client:
                response = await client.post(
                    ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._config.api_key}"},
                )
        except httpx.HTTPError as error:
            raise DeliveryError(f"resend could not be reached: {error}") from error

        return self._identifier(response)

    def _payload(self, email: Email, /) -> dict[str, Any]:
        """The JSON body for one message."""
        payload: dict[str, Any] = {
            "from": email.from_address,
            "to": list(email.to_addresses),
            "subject": email.subject,
            **self._content(email.body),
        }
        if email.cc_addresses:
            payload["cc"] = list(email.cc_addresses)
        if email.bcc_addresses:
            payload["bcc"] = list(email.bcc_addresses)
        if email.reply_to is not None:
            payload["reply_to"] = email.reply_to
        return payload

    @staticmethod
    def _content(body: EmailBody, /) -> dict[str, str]:
        """The one field carrying the body.

        A lookup rather than a chain of `if`s, so there is no final unreachable branch to
        either leave uncovered or excuse with a pragma — this repository has none.

        A plain-text body is sent as `text`, never as HTML wrapped in a paragraph. Wrapping it
        would mean the provider renders `<` in a password or a subject as markup, and the
        recipient sees something the sender never wrote.
        """
        return {RESEND_FIELD[body.type]: body.data}

    @staticmethod
    def _identifier(response: httpx.Response, /) -> str:
        """The provider's identifier for the message, or an explanation of what came back."""
        if response.is_error:
            raise DeliveryError(
                f"resend refused the message with {response.status_code}: {response.text}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise DeliveryError(
                f"resend answered {response.status_code} with a body that is not JSON."
            ) from error

        identifier = body.get("id") if isinstance(body, dict) else None
        if not isinstance(identifier, str):
            raise DeliveryError(
                f"resend accepted the message but named no id: {body!r}. "
                f"The message may or may not have been sent."
            )
        return identifier
