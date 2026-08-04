"""The one place this module reads the time.

Every expiry decision in `dexter.iam` goes through a `Clock`, and this is the implementation
that consults the machine. Keeping it to a single file is what makes the rest of the module
testable without sleeping, and what lets an application that already has its own notion of
"now" — a replay, a simulation, a test harness — supply it once and have dexter agree.

`datetime.now(UTC)` rather than `utcnow()`: the latter returns a *naive* datetime that claims
to be UTC, and comparing one against an aware datetime raises `TypeError`. That failure would
land precisely when a token expired, which is the worst possible place for it.
"""

from datetime import UTC, datetime


class SystemClock:
    """A `Clock` reading the machine's own clock, in UTC.

    Slotted and stateless: one per container, on the resolution path of every request that
    presents a token.
    """

    __slots__ = ()

    def now(self) -> datetime:
        """The current instant, in UTC and timezone-aware."""
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemClock()"
