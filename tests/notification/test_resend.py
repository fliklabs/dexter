"""The Resend engine: the request it builds, and what it does with every answer."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from dexter.notification import DeliveryError, EmailBody
from dexter.notification.resend import ENDPOINT, ResendConfig, ResendEmailNotifier

from .conftest import API_KEY, Exchange, accepted, make_config, make_email

type Build = Callable[..., ResendEmailNotifier]


class TestTheRequest:
    async def test_posts_the_message_to_the_send_endpoint(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(make_email())

        assert exchange.last.method == "POST"
        assert exchange.last.url.path == ENDPOINT

    async def test_carries_the_api_key_as_a_bearer_token(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(make_email())

        assert exchange.last.headers["authorization"] == f"Bearer {API_KEY}"

    async def test_sends_the_addresses_and_the_subject(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(
            make_email(
                from_address="a@b.com",
                to_addresses=("one@x.com", "two@x.com"),
                subject="Hello",
            )
        )

        assert exchange.payload["from"] == "a@b.com"
        assert exchange.payload["to"] == ["one@x.com", "two@x.com"]
        assert exchange.payload["subject"] == "Hello"

    async def test_sends_a_text_body_as_text(
        self, answering: Build, exchange: Exchange
    ) -> None:
        """Never wrapped in markup: a `<` in a code or a subject is not a tag."""
        await answering(accepted()).send(
            make_email(body=EmailBody.text("1 < 2 & you know it"))
        )

        assert exchange.payload["text"] == "1 < 2 & you know it"
        assert "html" not in exchange.payload

    async def test_sends_an_html_body_as_html(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(
            make_email(body=EmailBody.html("<p>Your code</p>"))
        )

        assert exchange.payload["html"] == "<p>Your code</p>"
        assert "text" not in exchange.payload

    async def test_omits_the_optional_fields_when_they_are_empty(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(make_email())

        assert "cc" not in exchange.payload
        assert "bcc" not in exchange.payload
        assert "reply_to" not in exchange.payload

    async def test_includes_the_optional_fields_when_they_are_set(
        self, answering: Build, exchange: Exchange
    ) -> None:
        await answering(accepted()).send(
            make_email(
                cc_addresses=("cc@x.com",),
                bcc_addresses=("bcc@x.com",),
                reply_to="reply@x.com",
            )
        )

        assert exchange.payload["cc"] == ["cc@x.com"]
        assert exchange.payload["bcc"] == ["bcc@x.com"]
        assert exchange.payload["reply_to"] == "reply@x.com"

    async def test_sends_to_the_configured_host(
        self, answering: Build, exchange: Exchange
    ) -> None:
        """A consumer pointing at a sandbox or a proxy changes one field."""
        await answering(accepted(), base_url="https://sandbox.example.com").send(
            make_email()
        )

        assert exchange.last.url.host == "sandbox.example.com"


class TestTheAnswer:
    async def test_returns_the_identifier_the_provider_named(
        self, answering: Build
    ) -> None:
        identifier = await answering(accepted("re_abc123")).send(make_email())

        assert identifier == "re_abc123"

    @pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 500, 503])
    async def test_a_refusal_is_a_delivery_error_naming_the_status(
        self, answering: Build, status: int
    ) -> None:
        notifier = answering(lambda _: httpx.Response(status, json={"message": "nope"}))

        with pytest.raises(DeliveryError, match=str(status)):
            await notifier.send(make_email())

    async def test_an_answer_that_is_not_json_is_a_delivery_error(
        self, answering: Build
    ) -> None:
        notifier = answering(lambda _: httpx.Response(200, text="<html>oops</html>"))

        with pytest.raises(DeliveryError, match="not JSON"):
            await notifier.send(make_email())

    async def test_an_answer_with_no_identifier_is_a_delivery_error(
        self, answering: Build
    ) -> None:
        """Accepted but unconfirmed, and the message says so — it may still have been sent."""
        notifier = answering(lambda _: httpx.Response(200, json={"ok": True}))

        with pytest.raises(DeliveryError, match="named no id"):
            await notifier.send(make_email())

    async def test_an_identifier_that_is_not_a_string_is_a_delivery_error(
        self, answering: Build
    ) -> None:
        notifier = answering(lambda _: httpx.Response(200, json={"id": 7}))

        with pytest.raises(DeliveryError, match="named no id"):
            await notifier.send(make_email())

    async def test_a_json_answer_that_is_not_an_object_is_a_delivery_error(
        self, answering: Build
    ) -> None:
        notifier = answering(lambda _: httpx.Response(200, json=["accepted"]))

        with pytest.raises(DeliveryError, match="named no id"):
            await notifier.send(make_email())

    async def test_an_unreachable_provider_is_a_delivery_error(
        self, answering: Build
    ) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with pytest.raises(DeliveryError, match="could not be reached"):
            await answering(refuse).send(make_email())

    async def test_a_timeout_is_a_delivery_error(self, answering: Build) -> None:
        def stall(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("took too long", request=request)

        with pytest.raises(DeliveryError, match="could not be reached"):
            await answering(stall).send(make_email())


class TestTheConfiguration:
    def test_refuses_an_empty_api_key(self) -> None:
        """A 401 from the provider is a poorer place to find this out."""
        with pytest.raises(ValueError, match="must not be empty"):
            make_config(api_key="   ")

    def test_refuses_an_unknown_field(self) -> None:
        misspelled: Any = {"api_key": API_KEY, "base_ur": "https://example.com"}

        with pytest.raises(ValueError, match="Extra inputs"):
            ResendConfig(**misspelled)

    def test_defaults_to_the_provider_s_own_host(self) -> None:
        assert make_config().base_url == "https://api.resend.com"

    def test_carries_a_timeout_so_a_send_cannot_hang_forever(self) -> None:
        assert make_config().timeout_seconds > 0
