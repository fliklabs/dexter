"""Issuing a code and checking it once.

Most of this file is the six guards `magic_code.py` documents, because a six-digit secret is
only safe if every one of them holds. The happy path is three lines.
"""

from datetime import timedelta
from typing import Any

import pytest

from dexter.iam import (
    DIGITS,
    InMemoryMagicCodeStore,
    MagicCode,
    MagicCodeExpiredError,
    MagicCodeMismatchError,
    MagicCodePolicy,
    MagicCodeService,
    MagicCodeThrottledError,
    NoMagicCodeError,
    TooManyAttemptsError,
)

from .conftest import SECRET, FrozenClock, make_code_policy, make_codes

KEY = "someone@example.com"


class TestIssuing:
    async def test_issues_a_code_of_the_configured_shape(self) -> None:
        code = await make_codes().issue(KEY)

        assert len(code) == 6
        assert set(code) <= set(DIGITS)

    async def test_a_longer_code_is_one_field(self) -> None:
        code = await make_codes(length=8).issue(KEY)

        assert len(code) == 8

    async def test_an_alphabet_without_ambiguous_characters_is_honoured(self) -> None:
        """`0`/`O` and `1`/`I` are the pair every support ticket is about."""
        code = await make_codes(alphabet="ACDEFHJKMNPRTWXY34789", length=6).issue(KEY)

        assert not set(code) & set("01OIl")

    async def test_issuing_replaces_an_outstanding_code(self) -> None:
        clock = FrozenClock()
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(), store, clock)

        first = await codes.issue(KEY)
        clock.advance(timedelta(minutes=1))
        second = await codes.issue(KEY)

        assert len(store) == 1
        with pytest.raises(MagicCodeMismatchError):
            await codes.verify(KEY, first)
        await codes.verify(KEY, second)

    async def test_two_keys_do_not_share_a_code(self) -> None:
        codes = make_codes()

        mine = await codes.issue("me@example.com")
        await codes.issue("you@example.com")

        await codes.verify("me@example.com", mine)


class TestTheStoreNeverHoldsTheCode:
    async def test_what_is_written_is_not_what_was_returned(self) -> None:
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(), store, FrozenClock())

        code = await codes.issue(KEY)
        record = await store.get(KEY)

        assert record is not None
        assert code not in record.digest

    async def test_the_same_code_digests_differently_for_a_different_key(self) -> None:
        """A digest lifted from one recipient's row cannot be replayed against another's."""
        policy = make_code_policy()
        first = MagicCodeService(policy, InMemoryMagicCodeStore(), FrozenClock())
        store = InMemoryMagicCodeStore()
        second = MagicCodeService(policy, store, FrozenClock())

        code = await first.issue("me@example.com")
        await second.issue("you@example.com")

        with pytest.raises(MagicCodeMismatchError):
            await second.verify("you@example.com", code)


class TestVerifying:
    async def test_a_correct_code_is_accepted(self) -> None:
        codes = make_codes()

        await codes.verify(KEY, await codes.issue(KEY))

    async def test_a_correct_code_works_only_once(self) -> None:
        """Single use is the service's guarantee, not a caller's convention."""
        codes = make_codes()
        code = await codes.issue(KEY)

        await codes.verify(KEY, code)

        with pytest.raises(NoMagicCodeError):
            await codes.verify(KEY, code)

    async def test_refuses_a_wrong_code(self) -> None:
        codes = make_codes()
        await codes.issue(KEY)

        with pytest.raises(MagicCodeMismatchError):
            await codes.verify(KEY, "000000")

    async def test_refuses_when_nothing_was_issued(self) -> None:
        with pytest.raises(NoMagicCodeError):
            await make_codes().verify(KEY, "123456")

    async def test_a_wrong_code_leaves_the_right_one_usable(self) -> None:
        codes = make_codes()
        code = await codes.issue(KEY)

        with pytest.raises(MagicCodeMismatchError):
            await codes.verify(KEY, _other_than(code))
        await codes.verify(KEY, code)


class TestExpiry:
    async def test_refuses_a_code_past_its_lifetime(self) -> None:
        clock = FrozenClock()
        codes = MagicCodeService(
            make_code_policy(ttl=timedelta(minutes=10)),
            InMemoryMagicCodeStore(),
            clock,
        )
        code = await codes.issue(KEY)

        clock.advance(timedelta(minutes=11))

        with pytest.raises(MagicCodeExpiredError):
            await codes.verify(KEY, code)

    async def test_an_expired_code_is_consumed_rather_than_left_behind(self) -> None:
        clock = FrozenClock()
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(), store, clock)
        code = await codes.issue(KEY)

        clock.advance(timedelta(hours=1))
        with pytest.raises(MagicCodeExpiredError):
            await codes.verify(KEY, code)

        assert len(store) == 0

    async def test_a_code_is_still_good_a_moment_before_it_dies(self) -> None:
        clock = FrozenClock()
        codes = MagicCodeService(
            make_code_policy(ttl=timedelta(minutes=10)),
            InMemoryMagicCodeStore(),
            clock,
        )
        code = await codes.issue(KEY)

        clock.advance(timedelta(minutes=10) - timedelta(seconds=1))

        await codes.verify(KEY, code)


class TestAttemptLimit:
    async def test_exhausting_the_allowance_destroys_the_code(self) -> None:
        """A million possibilities is an afternoon without this."""
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(max_attempts=3), store, FrozenClock())
        code = await codes.issue(KEY)
        wrong = _other_than(code)

        for _ in range(2):
            with pytest.raises(MagicCodeMismatchError):
                await codes.verify(KEY, wrong)
        with pytest.raises(TooManyAttemptsError):
            await codes.verify(KEY, wrong)

        assert len(store) == 0

    async def test_the_right_code_no_longer_works_once_the_allowance_is_gone(
        self,
    ) -> None:
        codes = make_codes(max_attempts=1)
        code = await codes.issue(KEY)

        with pytest.raises(TooManyAttemptsError):
            await codes.verify(KEY, _other_than(code))

        with pytest.raises(NoMagicCodeError):
            await codes.verify(KEY, code)

    async def test_an_allowance_of_one_refuses_the_first_wrong_guess(self) -> None:
        codes = make_codes(max_attempts=1)
        code = await codes.issue(KEY)

        with pytest.raises(TooManyAttemptsError):
            await codes.verify(KEY, _other_than(code))

    async def test_a_record_already_over_the_limit_is_swept_on_sight(self) -> None:
        """A store may hand back a record written by a run with a larger allowance."""
        clock = FrozenClock()
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(max_attempts=2), store, clock)
        await store.put(
            MagicCode(
                key=KEY,
                digest="whatever-was-written-before",
                issued_at=clock.now(),
                expires_at=clock.now() + timedelta(minutes=10),
                attempts=9,
            )
        )

        with pytest.raises(TooManyAttemptsError):
            await codes.verify(KEY, "123456")

        assert len(store) == 0


class TestThrottling:
    async def test_refuses_a_second_code_asked_for_too_soon(self) -> None:
        codes = make_codes(resend_after=timedelta(seconds=30))
        await codes.issue(KEY)

        with pytest.raises(MagicCodeThrottledError):
            await codes.issue(KEY)

    async def test_allows_a_second_code_once_the_window_has_passed(self) -> None:
        clock = FrozenClock()
        codes = MagicCodeService(
            make_code_policy(resend_after=timedelta(seconds=30)),
            InMemoryMagicCodeStore(),
            clock,
        )
        await codes.issue(KEY)

        clock.advance(timedelta(seconds=31))

        assert await codes.issue(KEY)

    async def test_throttling_is_per_key(self) -> None:
        codes = make_codes()
        await codes.issue("me@example.com")

        assert await codes.issue("you@example.com")

    async def test_a_zero_window_never_throttles(self) -> None:
        codes = make_codes(resend_after=timedelta(0))
        await codes.issue(KEY)

        assert await codes.issue(KEY)


class TestPolicy:
    def test_refuses_an_alphabet_with_one_character(self) -> None:
        with pytest.raises(ValueError, match="two distinct"):
            make_code_policy(alphabet="7")

    def test_refuses_an_alphabet_that_repeats_a_character(self) -> None:
        """A repeat weights that character, which is not what anybody meant to configure."""
        with pytest.raises(ValueError, match="must not repeat"):
            make_code_policy(alphabet="0123456789 0")

    def test_refuses_a_negative_window(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            make_code_policy(ttl=timedelta(seconds=-1))

    def test_refuses_a_code_too_short_to_be_worth_anything(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 4"):
            make_code_policy(length=3)

    def test_refuses_an_allowance_of_none(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            make_code_policy(max_attempts=0)

    def test_refuses_a_secret_shorter_than_the_floor(self) -> None:
        with pytest.raises(ValueError, match="at least 32"):
            make_code_policy(secret="too-short")

    def test_refuses_an_unknown_field(self) -> None:
        misspelled: Any = {"secret": SECRET, "lenght": 6}

        with pytest.raises(ValueError, match="Extra inputs"):
            MagicCodePolicy(**misspelled)


class TestTheStore:
    async def test_deleting_something_that_is_not_there_is_not_an_error(self) -> None:
        await InMemoryMagicCodeStore().delete("nothing")

    async def test_reports_how_many_are_outstanding(self) -> None:
        store = InMemoryMagicCodeStore()
        codes = MagicCodeService(make_code_policy(), store, FrozenClock())

        await codes.issue("me@example.com")
        await codes.issue("you@example.com")

        assert len(store) == 2
        assert "outstanding=2" in repr(store)

    async def test_repr_of_the_service_names_the_code_length(self) -> None:
        assert "length=6" in repr(make_codes())


def _other_than(code: str) -> str:
    """A code of the same shape that is definitely not `code`."""
    return "9" * len(code) if code != "9" * len(code) else "0" * len(code)
