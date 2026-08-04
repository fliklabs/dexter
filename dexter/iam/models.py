"""The contracts: who a caller is, what a token says, and what stores a code.

Frozen pydantic for everything a consumer builds or that arrives from outside — a policy read
from configuration, a principal built from a verified token — because validation earns its cost
exactly where a value crosses into dexter. `Protocol` for the two seams, so nothing has to
inherit from dexter to satisfy them.

**Every collection field is a tuple.** A frozen pydantic model is only shallowly frozen, so a
`list` field would leave the model mutable and silently unhashable.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}
"""The signature algorithms this module signs with, and the key length each one needs.

Deliberately a whitelist, and deliberately only the symmetric family. `none` is the classic JWT
forgery and must never be reachable from configuration. The asymmetric families are absent
because nothing here manages a key pair — offering `RS256` while accepting a single `secret`
string would be an invitation to sign with a public key.

The lengths are RFC 7518 §3.2: *"A key of the same size as the hash output ... or larger MUST be
used"*. They are enforced rather than warned about, which is the difference between a policy
that is wrong and an application that will not start.
"""

HMAC_ALGORITHMS = frozenset(KEY_BYTES)
"""Every algorithm `TokenPolicy` will accept."""

DIGITS = "0123456789"
"""The default alphabet for a magic code: what a person can read off a phone and retype."""

DISTINCT_CHARACTERS = 2
"""The fewest distinct characters an alphabet can have and still encode anything."""


class TokenKind(StrEnum):
    """What a token is for."""

    ACCESS = "ACCESS"
    """Presented on every request. Short-lived, because nothing can revoke it."""

    REFRESH = "REFRESH"
    """Exchanged for a new access token. Long-lived, and presented only to one route."""


def describe_token_kind(kind: TokenKind, /) -> str:
    """Render a token kind as the symbol a caller would type.

    `StrEnum.__str__` returns the bare value, which shouts in a sentence. In a message aimed at
    a developer the qualified symbol is more useful: it is what they have to write.
    """
    return f"TokenKind.{kind.name}"


class Claim(BaseModel):
    """One fact about a caller, carried inside their token.

    A pair rather than a mapping, because a frozen model with a `dict` field is neither frozen
    nor hashable. Values are strings: whatever goes in a token is read back by a different
    process, possibly a different version of this application, and a string is the only shape
    that survives that unambiguously.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str


class Principal(BaseModel):
    """Who a request is being made by.

    `subject` is whatever the application uses to name a caller for good — a user id, or an
    address when there is no user table yet. It ends up in the `sub` claim of every token and
    is the one thing a token is *about*, so it should be stable and it should not be a secret.

    Everything else goes in `claims`, and the rule for what belongs there is short: facts that
    are cheap, stable for the token's lifetime, and safe for the holder to read. A token is
    base64, not encryption — the caller can read every claim in it — and a claim that changes
    is a claim that stays stale until the token expires.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1)
    claims: tuple[Claim, ...] = ()

    def claim(self, name: str, /) -> str | None:
        """The value of the first claim called `name`, or `None` when there is none."""
        return next((c.value for c in self.claims if c.name == name), None)

    @classmethod
    def of(cls, subject: str, /, **claims: str) -> Self:
        """Build a principal from a subject and keyword claims.

        The convenience form: `Principal.of("a@b.com", role="admin")`. Claim order follows the
        keywords, which are ordered.
        """
        return cls(
            subject=subject,
            claims=tuple(
                Claim(name=name, value=value) for name, value in claims.items()
            ),
        )


class TokenPair(BaseModel):
    """What a successful login hands back.

    Both expiry instants are here because the client needs them. A browser that knows when its
    access token dies refreshes *before* a request fails; one that does not has to discover
    expiry by being refused, which turns every expiry into a user-visible error and a retry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


class TokenPolicy(BaseModel):
    """How tokens are signed and how long they live.

    Bound by the application with `register_token_policy`, never passed to `use_iam` — a
    `use_*` is a topology switch, not a settings object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: str = Field(min_length=32)
    """The signing key. Never shorter than the digest of `algorithm`, and that is not decoration.

    An HMAC key shorter than its digest is a key an offline attacker can search, and a JWT is
    the ideal target for that: the holder has both the message and the tag. A short key here is
    the difference between "they need the secret" and "they need a laptop". 32 is the floor for
    the default `HS256`; `KEY_BYTES` gives the requirement for each algorithm and the model
    checks the pair, because a secret long enough for one is short for another.
    """

    issuer: str = Field(min_length=1)
    """Written as `iss` and required to match on the way back in.

    It is what stops a token minted by one of your services being spent at another that happens
    to share a secret.
    """

    algorithm: str = "HS256"
    access_ttl: timedelta = timedelta(minutes=15)
    """How long an access token lives. Short, because nothing can revoke one."""

    refresh_ttl: timedelta = timedelta(days=30)
    """How long a refresh token lives, and so how long a login lasts."""

    leeway: timedelta = timedelta(seconds=30)
    """Clock skew tolerated when checking expiry.

    Two machines that disagree by a second should not reject each other's tokens; a browser and
    a server routinely disagree by more.
    """

    @field_validator("algorithm")
    @classmethod
    def _check_algorithm(cls, algorithm: str) -> str:
        """Refuse anything outside the symmetric family, `none` most of all."""
        if algorithm not in HMAC_ALGORITHMS:
            listed = ", ".join(sorted(HMAC_ALGORITHMS))
            raise ValueError(
                f"algorithm {algorithm!r} is not supported; use one of {listed}."
            )
        return algorithm

    @field_validator("access_ttl", "refresh_ttl")
    @classmethod
    def _check_lifetime(cls, ttl: timedelta) -> timedelta:
        """A token that has already expired when it is minted is never a deliberate choice."""
        if ttl <= timedelta(0):
            raise ValueError("a token lifetime must be positive.")
        return ttl

    @model_validator(mode="after")
    def _check_key_length(self) -> Self:
        """Reject a secret too short for the algorithm it will be used with.

        Cross-field, so it has to be a model validator: 32 characters is ample for `HS256` and
        half of what `HS512` requires. Left unchecked, PyJWT warns and signs anyway — and a
        warning in a library is something a consumer's log swallows.
        """
        required = KEY_BYTES[self.algorithm]
        length = len(self.secret.encode())
        if length < required:
            raise ValueError(
                f"secret is {length} bytes, but {self.algorithm} needs at least "
                f"{required}; a key shorter than the digest is searchable offline."
            )
        return self


class MagicCodePolicy(BaseModel):
    """How a magic code is generated, how long it lives, and how often it may be guessed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: str = Field(min_length=32)
    """Keys the digest a code is stored under, so the store never holds the code itself.

    May be the same value as `TokenPolicy.secret`; it is a separate field because they protect
    different things and an application that rotates one may not want to rotate the other.
    """

    length: int = Field(default=6, ge=4, le=32)
    alphabet: str = DIGITS
    ttl: timedelta = timedelta(minutes=10)
    """How long a code stays usable. Minutes: it is read from an inbox and typed straight in."""

    max_attempts: int = Field(default=5, ge=1)
    """How many wrong guesses a code survives before it is destroyed.

    This is what makes a six-digit code safe. Without it, a million guesses is an afternoon.
    """

    resend_after: timedelta = timedelta(seconds=30)
    """How soon a new code may be asked for. Guards someone else's inbox, not this process."""

    @field_validator("alphabet")
    @classmethod
    def _check_alphabet(cls, alphabet: str) -> str:
        """Reject an alphabet that cannot produce a code worth checking."""
        if len(set(alphabet)) < DISTINCT_CHARACTERS:
            raise ValueError("alphabet must contain at least two distinct characters.")
        if len(set(alphabet)) != len(alphabet):
            raise ValueError(
                "alphabet must not repeat a character; it would be weighted."
            )
        return alphabet

    @field_validator("ttl", "resend_after")
    @classmethod
    def _check_duration(cls, duration: timedelta) -> timedelta:
        """A negative window is never a deliberate choice."""
        if duration < timedelta(0):
            raise ValueError("a duration must not be negative.")
        return duration


class MagicCode(BaseModel):
    """One outstanding code, as it is held in a store.

    **The code itself is not in here.** `digest` is an HMAC of it, so a store — and anything
    that reads the store, including a backup and a log of one — holds something that cannot be
    presented. Verification compares digests.

    Frozen, so a store may cache one safely; the attempt counter advances by replacing the
    record rather than mutating it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    digest: str
    issued_at: datetime
    expires_at: datetime
    attempts: int = 0

    def attempted(self) -> Self:
        """The same record with one more failed attempt against it."""
        return self.model_copy(update={"attempts": self.attempts + 1})


class MagicCodeStore(Protocol):
    """Where outstanding codes are kept.

    Asynchronous because a real one is I/O — Redis, Postgres, DynamoDB. **One live code per
    key**: issuing replaces whatever was there, which is what makes "I asked twice, which code
    do I type?" have an answer.

    A store may expire records itself (a TTL column, or Redis' own) and is not required to; the
    service checks expiry on every read, so an implementation that never sweeps is correct,
    just untidy.
    """

    async def get(self, key: str) -> MagicCode | None:
        """The record for `key`, or `None` when there is none."""
        ...

    async def put(self, code: MagicCode) -> None:
        """Store `code`, replacing any record under the same key."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the record for `key`. Removing one that is not there is not an error."""
        ...


class Clock(Protocol):
    """What the module asks for the time.

    A seam rather than a direct `datetime.now(UTC)`, and it earns its place twice: expiry is
    most of the behaviour here and testing it against the real clock would mean sleeping, and
    an application that already has a clock — a simulation, a replay, a test harness — gets one
    consistent notion of "now" across its own code and dexter's.

    Every implementation must return an **aware** datetime. A naive one compares unpredictably
    against an aware one, which is a `TypeError` at the exact moment a token expires.
    """

    def now(self) -> datetime:
        """The current instant, with a timezone."""
        ...
