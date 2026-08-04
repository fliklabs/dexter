"""Issuing a short code and checking it once.

A magic code is a password with an expiry, a tiny keyspace and exactly one use. Everything in
here follows from that, and each of the six rules below closes something the obvious
implementation leaves open — `i/iam/authentication/otp` in itamoo-app being the obvious
implementation, and a fair one to measure against because it is in production.

1. **Codes come from `secrets`, never `random`.** `random` is a Mersenne twister: observing a
   few hundred outputs recovers its state, and then every future code is known before it is
   sent. This is the failure that leaves no trace at all.
2. **The store never holds the code.** What is written is an HMAC of it, keyed by the policy's
   secret and bound to the key it was issued for, so a database dump — or a backup, or a log
   line, or a support engineer with read access — yields nothing presentable.
3. **Comparison is `hmac.compare_digest`.** `!=` on a string returns as soon as two characters
   differ, and the time it took says how much of the guess was right.
4. **Expiry is checked before the comparison.** Otherwise an expired code and a wrong code take
   measurably different paths, and the difference says whether a code was ever issued.
5. **Attempts are counted, and exhausting them destroys the code.** Six digits is a million
   possibilities; without this, guessing is an afternoon's work. Destroying rather than locking
   is deliberate: a lock is a denial-of-service handle on somebody else's address.
6. **A successful verification consumes the code, here.** itamoo leaves that to the caller — it
   verifies, then the handler separately clears — so a crash between the two leaves a live
   code, and every new call site is a chance to forget. Single use is this service's guarantee
   or it is nobody's.

**Asynchronous, because a real store is I/O.** The service itself computes an HMAC and nothing
else; it is a coroutine so that swapping the in-memory store for Redis changes one binding.
"""

import hmac
import secrets
from hashlib import sha256

from .errors import (
    MagicCodeExpiredError,
    MagicCodeMismatchError,
    MagicCodeThrottledError,
    NoMagicCodeError,
    TooManyAttemptsError,
)
from .models import Clock, MagicCode, MagicCodePolicy, MagicCodeStore


class MagicCodeService:
    """Issues one-time codes and verifies them.

    Slotted: one per container, and it holds only its collaborators.
    """

    __slots__ = ("_clock", "_policy", "_store")

    def __init__(
        self, policy: MagicCodePolicy, store: MagicCodeStore, clock: Clock
    ) -> None:
        """Record the policy, where codes are kept, and what the time is."""
        self._policy = policy
        self._store = store
        self._clock = clock

    async def issue(self, key: str, /) -> str:
        """Generate a code for `key`, store its digest, and return the code.

        The code is returned **once, here**. Nothing else can recover it, which is the point of
        storing a digest — so whatever sends it to the caller has to do so from this return
        value, and an application that logs it has made that choice explicitly.

        Issuing replaces any outstanding code for the same key, so "I requested two, which one
        works?" has an answer: the newer.

        Raises:
            MagicCodeThrottledError: If a code was issued for this key less than
                `resend_after` ago. Someone else owns the inbox this lands in.
        """
        now = self._clock.now()
        existing = await self._store.get(key)
        if (
            existing is not None
            and now < existing.issued_at + self._policy.resend_after
        ):
            raise MagicCodeThrottledError(
                "a code was issued for this recipient too recently."
            )

        code = self._generate()
        await self._store.put(
            MagicCode(
                key=key,
                digest=self._digest(key, code),
                issued_at=now,
                expires_at=now + self._policy.ttl,
            )
        )
        return code

    async def verify(self, key: str, code: str, /) -> None:
        """Check `code` against the one outstanding for `key`, consuming it on success.

        Returns nothing: there is one way to succeed and several to fail, so the answer is the
        absence of an exception.

        Raises:
            NoMagicCodeError: If no code is outstanding for this key.
            MagicCodeExpiredError: If there was one and its lifetime has passed.
            TooManyAttemptsError: If this guess was the one that exhausted the allowance.
            MagicCodeMismatchError: If the code is wrong and guesses remain.

        **Do not report which of these happened to the caller.** The difference between "no
        code" and "wrong code" tells an unauthenticated stranger whether an address has an
        account here. An application should answer all four the same way and log the
        distinction.
        """
        record = await self._store.get(key)
        if record is None:
            raise NoMagicCodeError("no code is outstanding for this recipient.")

        # Before the comparison, so that an expired code and a wrong code cannot be told apart
        # by how long the answer took.
        if self._clock.now() >= record.expires_at:
            await self._store.delete(key)
            raise MagicCodeExpiredError("the code has expired.")

        if record.attempts >= self._policy.max_attempts:
            await self._store.delete(key)
            raise TooManyAttemptsError("the code has been guessed at too many times.")

        if not hmac.compare_digest(record.digest, self._digest(key, code)):
            attempted = record.attempted()
            if attempted.attempts >= self._policy.max_attempts:
                await self._store.delete(key)
                raise TooManyAttemptsError(
                    "the code has been guessed at too many times."
                )
            await self._store.put(attempted)
            raise MagicCodeMismatchError("that code is not the one issued.")

        # Consumed here rather than by the caller, so single use is a property of the service.
        await self._store.delete(key)

    def _generate(self) -> str:
        """A code of the configured length, drawn from a cryptographic source."""
        alphabet = self._policy.alphabet
        return "".join(secrets.choice(alphabet) for _ in range(self._policy.length))

    def _digest(self, key: str, code: str, /) -> str:
        """The stored form of `code`.

        The key is mixed in, so a digest lifted from one recipient's row cannot be replayed
        against another's — which matters here in a way it would not for a long password,
        because with six digits the same code recurs constantly.
        """
        return hmac.new(
            self._policy.secret.encode(),
            f"{key}:{code}".encode(),
            sha256,
        ).hexdigest()

    def __repr__(self) -> str:
        return f"MagicCodeService(length={self._policy.length})"
