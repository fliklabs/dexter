"""The message contract, the recording notifier, and the wiring that binds one."""

from typing import Any

import pytest

from dexter.dependency_injection import ContainerBuilder, DuplicateRegistrationError
from dexter.notification import (
    Email,
    EmailBody,
    EmailBodyType,
    EmailNotifier,
    RecordingEmailNotifier,
    describe_body_type,
    use_recording_notification,
)
from dexter.notification.resend import (
    ResendEmailNotifier,
    register_resend_config,
    use_resend_notification,
)

from .conftest import make_config, make_email


class TestTheMessage:
    def test_refuses_a_message_nobody_would_receive(self) -> None:
        with pytest.raises(ValueError, match="at least one recipient"):
            make_email(to_addresses=())

    def test_refuses_a_message_with_no_sender(self) -> None:
        with pytest.raises(ValueError, match="must name a sender"):
            make_email(from_address="   ")

    def test_refuses_an_unknown_field(self) -> None:
        misspelled: Any = {
            "from_address": "a@b.com",
            "to_addresses": ("c@d.com",),
            "subject": "hi",
            "body": EmailBody.text("x"),
            "reply_too": "e@f.com",
        }

        with pytest.raises(ValueError, match="Extra inputs"):
            Email(**misspelled)

    def test_is_hashable_so_it_can_be_put_in_a_set(self) -> None:
        """Frozen is only shallow, so a `list` field would silently break this."""
        assert len({make_email(), make_email()}) == 1

    def test_cannot_be_changed_after_it_is_built(self) -> None:
        # An `Any`-typed local rather than a `# type: ignore`, which `warn_unused_ignores`
        # would flag: pydantic already types a frozen model's fields as read-only.
        email: Any = make_email()

        with pytest.raises(ValueError, match="frozen"):
            email.subject = "something else"

    def test_text_and_html_are_the_two_ways_to_build_a_body(self) -> None:
        assert EmailBody.text("x").type is EmailBodyType.TEXT
        assert EmailBody.html("<p>x</p>").type is EmailBodyType.HTML

    def test_describes_a_body_type_as_the_symbol_a_reader_would_type(self) -> None:
        assert describe_body_type(EmailBodyType.HTML) == "EmailBodyType.HTML"

    def test_every_body_type_writes_its_own_name_as_its_value(self) -> None:
        """One canonical spelling, so `Kind(x) is Kind[x]` holds everywhere."""
        assert all(member.value == member.name for member in EmailBodyType)


class TestTheRecordingNotifier:
    async def test_records_what_it_was_asked_to_send(self) -> None:
        notifier = RecordingEmailNotifier()
        email = make_email(subject="Your code")

        await notifier.send(email)

        assert notifier.sent == [email]
        assert notifier.last == email

    async def test_names_each_message_distinctly(self) -> None:
        notifier = RecordingEmailNotifier()

        first = await notifier.send(make_email())
        second = await notifier.send(make_email())

        assert first != second

    def test_has_sent_nothing_to_start_with(self) -> None:
        notifier = RecordingEmailNotifier()

        assert notifier.sent == []
        assert notifier.last is None

    async def test_can_be_emptied_between_scenarios(self) -> None:
        notifier = RecordingEmailNotifier()
        await notifier.send(make_email())

        notifier.clear()

        assert notifier.sent == []

    async def test_repr_counts_what_it_has(self) -> None:
        notifier = RecordingEmailNotifier()
        await notifier.send(make_email())

        assert "sent=1" in repr(notifier)


class TestWiring:
    async def test_the_recording_topology_binds_one_object_under_both_keys(
        self,
    ) -> None:
        """Two singletons would record into one list and be read from another."""
        builder = ContainerBuilder()
        use_recording_notification(builder)
        container = builder.build()
        try:
            assert await container.resolve(EmailNotifier) is await container.resolve(
                RecordingEmailNotifier
            )
        finally:
            await container.aclose()

    async def test_a_message_sent_through_the_contract_is_readable_from_the_recorder(
        self,
    ) -> None:
        builder = ContainerBuilder()
        use_recording_notification(builder)
        container = builder.build()
        try:
            await (await container.resolve(EmailNotifier)).send(make_email())
            recorder = await container.resolve(RecordingEmailNotifier)
        finally:
            await container.aclose()

        assert len(recorder.sent) == 1

    async def test_the_resend_topology_binds_one_object_under_both_keys(self) -> None:
        builder = ContainerBuilder()
        use_resend_notification(builder)
        register_resend_config(builder, make_config())
        container = builder.build()
        try:
            assert await container.resolve(EmailNotifier) is await container.resolve(
                ResendEmailNotifier
            )
        finally:
            await container.aclose()

    def test_two_engines_cannot_both_be_chosen(self) -> None:
        """The right failure: the alternative is real mail sent from a test suite."""
        builder = ContainerBuilder()
        use_recording_notification(builder)

        with pytest.raises(DuplicateRegistrationError):
            use_resend_notification(builder)
