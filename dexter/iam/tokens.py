"""Minting and reading the two tokens a login produces.

This file owns the claim shape; `jwt_codec.py` owns the signing. What it writes is ordinary
registered JWT claims at the top level — `sub`, `iss`, `iat`, `exp`, `jti` — plus two of its
own, `kind` and `claims`. itamoo's equivalent nests the entire payload under a `"data"` key,
which means no JWT debugger, no gateway and no other language's library can read a token
without being told about the wrapper. There is nothing to gain from that and a decade of
tooling to lose.

**Synchronous, deliberately.** dexter is async-native about I/O, and there is none here: an
HMAC over a few hundred bytes is arithmetic. Making these coroutines would suggest a cost that
does not exist and force every caller to await one.

**Refresh is stateless.** `verify_refresh` checks the signature, the issuer, the expiry and the
kind, and consults nothing. That is the whole trade: no store to run, and **no revocation** —
a refresh token stays good until it expires, so logging out clears the client and nothing else.
Backing it with a session store later means giving `TokenService` a `SessionStore` and one
lookup in `verify_refresh`; the token already carries `jti`, which is the handle such a store
would key on, so nothing minted today becomes unreadable then.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import ExpiredTokenError, InvalidTokenError, WrongTokenKindError
from .jwt_codec import JwtCodec
from .models import Claim, Clock, Principal, TokenKind, TokenPair, TokenPolicy

CLAIM_PAIR = 2
"""How many elements a serialised claim has: a name and a value."""


class TokenService:
    """Mints access and refresh tokens, and reads them back.

    Constructed by the container from a `TokenPolicy` the application bound and a `Clock`. It
    holds one codec, built once, because building one per call would re-read the policy on the
    resolution path of every authenticated request.
    """

    __slots__ = ("_clock", "_codec", "_policy")

    def __init__(self, policy: TokenPolicy, clock: Clock) -> None:
        """Record the policy and build the codec it describes."""
        self._policy = policy
        self._clock = clock
        self._codec = JwtCodec(
            secret=policy.secret,
            algorithm=policy.algorithm,
            issuer=policy.issuer,
        )

    # ── minting ──────────────────────────────────────────────────────

    def mint(self, principal: Principal, /) -> TokenPair:
        """Issue both tokens for `principal`, with the lifetimes the policy sets.

        One instant is read from the clock and used for both, so the pair is internally
        consistent — two reads could straddle a second and produce a refresh token that appears
        to have been issued before the access token it accompanies.
        """
        issued_at = self._now()
        access, access_expires_at = self._mint(
            principal, TokenKind.ACCESS, issued_at, self._policy.access_ttl
        )
        refresh, refresh_expires_at = self._mint(
            principal, TokenKind.REFRESH, issued_at, self._policy.refresh_ttl
        )
        return TokenPair(
            access_token=access,
            access_token_expires_at=access_expires_at,
            refresh_token=refresh,
            refresh_token_expires_at=refresh_expires_at,
        )

    def mint_access(self, principal: Principal, /) -> tuple[str, datetime]:
        """Issue one access token, and say when it dies.

        This is what a refresh endpoint calls. The expiry is returned rather than left for the
        caller to recompute, because a client that has to guess when its token expires ends up
        discovering it from a 401.
        """
        return self._mint(
            principal, TokenKind.ACCESS, self._now(), self._policy.access_ttl
        )

    # ── reading ──────────────────────────────────────────────────────

    def verify_access(self, token: str, /) -> Principal:
        """Read an access token, or explain why it cannot be read.

        Raises:
            ExpiredTokenError: If its lifetime has passed.
            WrongTokenKindError: If it is a refresh token.
            InvalidTokenError: If it is not a token this application issued.
        """
        return self._verify(token, TokenKind.ACCESS)

    def verify_refresh(self, token: str, /) -> Principal:
        """Read a refresh token, or explain why it cannot be read.

        Raises:
            ExpiredTokenError: If its lifetime has passed.
            WrongTokenKindError: If it is an access token. This direction matters less than the
                other, but a client sending the wrong one should be told so rather than be
                refused for a reason that reads like a bad password.
            InvalidTokenError: If it is not a token this application issued.
        """
        return self._verify(token, TokenKind.REFRESH)

    # ── internals ────────────────────────────────────────────────────

    def _mint(
        self,
        principal: Principal,
        kind: TokenKind,
        issued_at: datetime,
        ttl: timedelta,
        /,
    ) -> tuple[str, datetime]:
        """Sign one token of `kind`, and return it with its expiry."""
        expires_at = issued_at + ttl
        claims: dict[str, Any] = {
            "sub": principal.subject,
            "kind": str(kind),
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            # A unique id per token. Nothing reads it today; it is what a revocation list or a
            # session store would key on, and adding it later would leave every token already
            # in circulation unrevokable.
            "jti": uuid.uuid4().hex,
        }
        if principal.claims:
            claims["claims"] = [[c.name, c.value] for c in principal.claims]
        return self._codec.encode(claims), expires_at

    def _verify(self, token: str, expected: TokenKind, /) -> Principal:
        """Decode `token`, check it is the kind asked for, and rebuild its principal."""
        claims = self._codec.decode(token)

        kind = claims.get("kind")
        if kind != str(expected):
            raise WrongTokenKindError(
                f"a {expected.name.lower()} token was required here."
            )

        # The only expiry check there is. `JwtCodec` requires `exp` to be present and refuses
        # to judge it, so that an application running on its own notion of time — and every
        # test of expiry — gets the answer it asked for rather than the machine's.
        expires_at = self._instant(claims, "exp")
        if expires_at <= self._now() - self._policy.leeway:
            raise ExpiredTokenError("the token has expired.")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidTokenError("the token names no subject.")

        return Principal(subject=subject, claims=self._read_claims(claims))

    @staticmethod
    def _read_claims(claims: dict[str, Any], /) -> tuple[Claim, ...]:
        """Rebuild a principal's claims, refusing anything that is not a pair of strings.

        A token is signed, so this content is ours — but "ours" includes a version of this
        application that wrote a different shape, and a decoder that trusts its own past is how
        a rollback turns into a crash on the request path.
        """
        raw = claims.get("claims")
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise InvalidTokenError("the token's claims are not a list.")
        read: list[Claim] = []
        for item in raw:
            if not (isinstance(item, list) and len(item) == CLAIM_PAIR):
                raise InvalidTokenError(
                    "a claim in the token is not a name and a value."
                )
            name, value = item
            if not (isinstance(name, str) and isinstance(value, str)):
                raise InvalidTokenError(
                    "a claim in the token is not a pair of strings."
                )
            read.append(Claim(name=name, value=value))
        return tuple(read)

    @staticmethod
    def _instant(claims: dict[str, Any], name: str, /) -> datetime:
        """Read a numeric claim as an aware datetime."""
        value = claims.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise InvalidTokenError(f"the token's {name} claim is not a number.")
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise InvalidTokenError(
                f"the token's {name} claim is out of range."
            ) from error

    def _now(self) -> datetime:
        """The current instant, from the injected clock."""
        return self._clock.now()

    def __repr__(self) -> str:
        return f"TokenService(issuer={self._policy.issuer!r})"
