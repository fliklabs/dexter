"""Remembering what a service said for a while, and asking it only once.

Two clients need this and neither should own it. A value resolved from Secrets Manager or the
parameter store is read on *every* call that needs it, so an uncached implementation turns each
request into an extra network round-trip and, for secrets, a per-request charge. The reference
library this module improves on wrote exactly this cache for its parameter client and then never
applied it to its secrets client — which is the argument for one implementation rather than two
copies, made by the thing that happened when there were two.

**One fetch at a time per key.** Ten handlers starting on a cold cache must not send ten
identical requests and race over which answer is stored. The mechanism is a shared task rather
than a lock, and the difference shows up on the failure path:

- With a lock and a re-check, ten waiters queue; the first fails; the second acquires, re-checks,
  finds nothing, and fetches again. A denial or a throttle becomes ten sequential retries with
  the caller waiting for all of them.
- With one shared task, all ten await the same fetch and all ten receive the same exception, at
  once. Which is also the behaviour a caller would describe if asked.

It is `asyncio.shield`ed because the first caller is not special: if that one is cancelled — a
dropped request, a timeout — the other nine are still waiting on the fetch, and cancelling it
under them would be punishing them for somebody else's disconnect.

**Unbounded, and only defensibly so.** Keys come from wiring: a handful of secret names and
parameter names, fixed at startup. A cache keyed on anything a request carries would leak, so
if that ever becomes true this needs an eviction policy rather than a bigger comment.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable


class TtlCache[T]:
    """Values that expire, fetched at most once at a time per key."""

    __slots__ = ("_entries", "_in_flight", "_ttl_seconds")

    def __init__(self, ttl_seconds: float) -> None:
        """Start empty.

        Args:
            ttl_seconds: How long a stored value stays good. Zero expires immediately, which is
                how expiry is tested without waiting for it.
        """
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, T]] = {}
        self._in_flight: dict[str, asyncio.Task[T]] = {}

    async def get(self, key: str, fetch: Callable[[], Awaitable[T]], /) -> T:
        """The cached value for `key`, fetching it if there is none.

        Args:
            key: What is being remembered.
            fetch: How to obtain it. Called at most once per key per expiry, however many
                callers arrive together.

        Returns:
            The value, from cache or freshly fetched.

        Raises:
            Exception: Whatever `fetch` raised, delivered to every caller waiting on it. A
                failed fetch is **not** stored — a transient denial must not poison the cache
                for the life of the process.
        """
        cached = self._fresh(key)
        if cached is not None:
            return cached[0]

        task = self._in_flight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fill(key, fetch))
            self._in_flight[key] = task
        return await asyncio.shield(task)

    def peek(self, key: str, /) -> tuple[T] | None:
        """The cached value in a one-tuple, or `None` if there is none that is still good.

        For a caller that fetches in batches and needs to know which keys to ask for before
        asking. It never fetches and never waits, which is what makes it usable in the middle
        of assembling a request.

        Wrapped in a tuple rather than returned bare, because `None` is a legitimate value to
        cache and would otherwise be indistinguishable from a miss.
        """
        return self._fresh(key)

    def put(self, key: str, value: T, /) -> None:
        """Store `value` under `key`, starting its lifetime now.

        For the same batch caller: one request answers many keys, and each of them should be
        remembered as if it had been fetched on its own.
        """
        self._entries[key] = (time.monotonic() + self._ttl_seconds, value)

    def invalidate(self, key: str | None = None, /) -> None:
        """Forget `key`, or everything when no key is given.

        The operator path for a rotation that must take effect before the lifetime is up. It
        performs no I/O, so it is deliberately not `async`: making it a coroutine would suggest
        it talks to AWS, and the next question would be what happens when that fails.

        An in-flight fetch is left alone. It was started before this call and its answer is no
        more stale than the moment it was asked for; cancelling it would fail callers who are
        already waiting, to no purpose.
        """
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    async def _fill(self, key: str, fetch: Callable[[], Awaitable[T]], /) -> T:
        """Fetch one value, store it if it arrives, and stop being the in-flight fetch."""
        try:
            value = await fetch()
        finally:
            # Popped in `finally` rather than after a successful store, so a failure does not
            # leave a completed task parked as the in-flight one — which would hand the same
            # exception to every later caller forever.
            self._in_flight.pop(key, None)
        self.put(key, value)
        return value

    def _fresh(self, key: str, /) -> tuple[T] | None:
        """The cached value in a one-tuple if it has not expired, otherwise `None`.

        Wrapped rather than returned bare, because `None` and `False` are legitimate values to
        cache and would otherwise be indistinguishable from a miss.

        `time.monotonic` rather than a wall clock: an entry outliving an NTP correction or a
        daylight-saving change is not a failure anybody would enjoy diagnosing.
        """
        entry = self._entries.get(key)
        if entry is None or entry[0] <= time.monotonic():
            return None
        return (entry[1],)
