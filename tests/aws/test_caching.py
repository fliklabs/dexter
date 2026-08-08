"""Covers `dexter.aws._caching`: expiry, single flight, and what a failure does not do.

Driven directly rather than through a client. The cache is the piece both value clients depend
on, and the properties that matter — one fetch for ten waiters, a failure that is not stored —
are invisible through a client's surface.

**The single-flight tests hold the fetch open, and they have to.** The obvious version, ten
gathered reads against a counter, passes with the whole mechanism deleted: the first read
completes before the others are scheduled, so they all hit the plain cache check and never reach
the in-flight path at all. Every test here that claims something about concurrency blocks the
first caller until the rest have arrived.
"""

import asyncio

import pytest

from dexter.aws import TtlCache


class Fetches:
    """A fetch that counts, and can be held open until a test lets it finish."""

    def __init__(self, value: str = "value") -> None:
        self.value = value
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self) -> str:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.value

    def hold(self) -> None:
        """Make the next fetch block until `let_go` is called."""
        self.release.clear()

    def let_go(self) -> None:
        self.release.set()


async def failing() -> str:
    """A fetch that always refuses."""
    raise RuntimeError("the service said no")


class TestReading:
    async def test_fetches_when_there_is_nothing_stored(self) -> None:
        fetch = Fetches("first")
        assert await TtlCache[str](60.0).get("k", fetch) == "first"
        assert fetch.calls == 1

    async def test_a_second_read_does_not_fetch_again(self) -> None:
        cache = TtlCache[str](60.0)
        fetch = Fetches()

        assert await cache.get("k", fetch) == "value"
        assert await cache.get("k", fetch) == "value"
        assert fetch.calls == 1

    async def test_two_keys_do_not_share_an_entry(self) -> None:
        cache = TtlCache[str](60.0)

        assert await cache.get("a", Fetches("a-value")) == "a-value"
        assert await cache.get("b", Fetches("b-value")) == "b-value"

    async def test_a_falsy_value_is_still_a_hit(self) -> None:
        """Why `_fresh` wraps its answer in a tuple.

        An empty string is a legitimate parameter value. Returned bare it would be
        indistinguishable from a miss, and every read would fetch again.
        """
        cache = TtlCache[str](60.0)
        fetch = Fetches("")

        assert await cache.get("k", fetch) == ""
        assert await cache.get("k", fetch) == ""
        assert fetch.calls == 1


class TestExpiry:
    async def test_refetches_once_the_entry_has_expired(self) -> None:
        """A zero lifetime expires immediately, so expiry is tested without waiting for it."""
        cache = TtlCache[str](0.0)
        fetch = Fetches()

        await cache.get("k", fetch)
        await cache.get("k", fetch)
        assert fetch.calls == 2

    async def test_invalidating_one_key_leaves_the_others(self) -> None:
        cache = TtlCache[str](60.0)
        first, second = Fetches("a"), Fetches("b")
        await cache.get("a", first)
        await cache.get("b", second)

        cache.invalidate("a")

        await cache.get("a", first)
        await cache.get("b", second)
        assert (first.calls, second.calls) == (2, 1)

    async def test_invalidating_everything_clears_every_key(self) -> None:
        cache = TtlCache[str](60.0)
        first, second = Fetches("a"), Fetches("b")
        await cache.get("a", first)
        await cache.get("b", second)

        cache.invalidate()

        await cache.get("a", first)
        await cache.get("b", second)
        assert (first.calls, second.calls) == (2, 2)


class TestPeekAndPut:
    async def test_peek_answers_none_for_an_unknown_key(self) -> None:
        assert TtlCache[str](60.0).peek("k") is None

    async def test_put_makes_a_later_read_a_hit(self) -> None:
        """What lets a batch fetch record every value it received as if each were its own."""
        cache = TtlCache[str](60.0)
        fetch = Fetches()
        cache.put("k", "stored")

        assert await cache.get("k", fetch) == "stored"
        assert fetch.calls == 0

    async def test_a_put_value_expires_like_any_other(self) -> None:
        cache = TtlCache[str](0.0)
        cache.put("k", "stored")
        assert cache.peek("k") is None


class TestSingleFlight:
    async def test_ten_callers_arriving_together_fetch_once(self) -> None:
        """**The property the in-flight task exists for.**

        The fetch is held open so that the other nine readers reach the in-flight check with the
        cache still empty — the exact state the mechanism is for. Without holding it, the first
        read would finish before the rest were scheduled and all ten would hit the plain cache
        check, which is a test that passes with the mechanism removed.
        """
        cache = TtlCache[str](60.0)
        fetch = Fetches()
        fetch.hold()

        readers = asyncio.gather(*(cache.get("k", fetch) for _ in range(10)))
        await fetch.started.wait()
        fetch.let_go()

        assert await readers == ["value"] * 10
        assert fetch.calls == 1

    async def test_every_waiter_sees_the_same_failure(self) -> None:
        """A lock with a re-check would instead retry the failing fetch once per waiter."""
        cache = TtlCache[str](60.0)
        calls = 0
        release = asyncio.Event()

        async def fetch() -> str:
            nonlocal calls
            calls += 1
            await release.wait()
            raise RuntimeError("the service said no")

        readers = asyncio.gather(
            *(cache.get("k", fetch) for _ in range(5)), return_exceptions=True
        )
        await asyncio.sleep(0)
        release.set()

        results = await readers
        assert calls == 1
        assert all(isinstance(result, RuntimeError) for result in results)

    async def test_cancelling_one_caller_does_not_cancel_the_fetch(self) -> None:
        """Why the shared task is shielded.

        The first caller going away — a dropped request, a timeout — must not fail the nine
        others that are waiting on the same fetch.
        """
        cache = TtlCache[str](60.0)
        fetch = Fetches()
        fetch.hold()

        leaving = asyncio.ensure_future(cache.get("k", fetch))
        staying = asyncio.ensure_future(cache.get("k", fetch))
        await fetch.started.wait()
        leaving.cancel()
        fetch.let_go()

        assert await staying == "value"
        assert fetch.calls == 1

    async def test_a_failed_fetch_is_not_cached(self) -> None:
        """A transient denial must not poison the cache for the life of the process."""
        cache = TtlCache[str](60.0)

        with pytest.raises(RuntimeError):
            await cache.get("k", failing)

        assert await cache.get("k", Fetches("recovered")) == "recovered"

    async def test_a_failed_fetch_leaves_no_in_flight_task_behind(self) -> None:
        """Otherwise the completed, failed task stays parked and every later caller re-raises
        the same exception forever."""
        cache = TtlCache[str](60.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cache.get("k", failing)

        assert await cache.get("k", Fetches("recovered")) == "recovered"
