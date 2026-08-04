"""Fixtures local to the notification tests.

The Resend engine is driven through `httpx.MockTransport`, which answers a request without a
socket and hands the test the request that was made. That is the only way to assert on the
payload the notifier builds — the thing a provider would actually receive — rather than on a
call signature a mock recorded.

The transport is installed by monkeypatching `httpx.AsyncClient` for the duration of one test.
The notifier deliberately opens its own client, so injecting one would mean adding a seam that
exists only for the tests and then exercising code that is not what ships.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from dexter.notification import Email, EmailBody
from dexter.notification.resend import ResendConfig, ResendEmailNotifier

API_KEY = "re_test_0123456789"

type Answer = Callable[[httpx.Request], httpx.Response]
"""What a test says the provider replies with."""


def make_email(**overrides: Any) -> Email:
    """A message every field of which a test may replace."""
    fields: dict[str, Any] = {
        "from_address": "Plum <noreply@example.com>",
        "to_addresses": ("someone@example.com",),
        "subject": "Your code",
        "body": EmailBody.text("Your code is 123456."),
    }
    fields.update(overrides)
    return Email(**fields)


def make_config(**overrides: Any) -> ResendConfig:
    """A configuration every field of which a test may replace."""
    fields: dict[str, Any] = {"api_key": API_KEY}
    fields.update(overrides)
    return ResendConfig(**fields)


def accepted(identifier: str = "msg-1") -> Answer:
    """A provider that accepts the message and names an id."""
    return lambda _: httpx.Response(200, json={"id": identifier})


class Exchange:
    """Every request the notifier made, for a test to assert on."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.requests: list[httpx.Request] = []

    @property
    def last(self) -> httpx.Request:
        """The most recent request."""
        return self.requests[-1]

    @property
    def payload(self) -> dict[str, Any]:
        """The JSON body of the most recent request."""
        decoded: dict[str, Any] = json.loads(self.last.content)
        return decoded


@pytest.fixture
def exchange() -> Exchange:
    """A fresh record of what was sent."""
    return Exchange()


@pytest.fixture
def answering(
    monkeypatch: pytest.MonkeyPatch, exchange: Exchange
) -> Callable[..., ResendEmailNotifier]:
    """Build a notifier whose provider is `answer`, recording every request it receives."""

    def build(answer: Answer, **config: Any) -> ResendEmailNotifier:
        def record(request: httpx.Request) -> httpx.Response:
            exchange.requests.append(request)
            return answer(request)

        real = httpx.AsyncClient

        def client(**kwargs: Any) -> httpx.AsyncClient:
            return real(**kwargs, transport=httpx.MockTransport(record))

        monkeypatch.setattr(httpx, "AsyncClient", client)
        return ResendEmailNotifier(make_config(**config))

    return build
