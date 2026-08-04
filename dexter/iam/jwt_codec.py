"""Signing and verifying JWTs — the only file in dexter that imports `jwt`.

Deliberately low level: it takes a mapping of claims and returns a string, and it takes a string
and returns a mapping. It knows nothing about principals, access tokens or refresh tokens —
`tokens.py` owns that shape. The split is what keeps the library's one piece of cryptography in
a file small enough to read in a sitting, and it is what makes the boundary test meaningful.

Three things it does that a naive wrapper does not, each of them a real forgery it closes:

- **The algorithm is passed to `decode` as a whitelist of one.** PyJWT will otherwise trust the
  `alg` header the *token* carries, and `alg: none` is a token anybody can write.
- **`iss` is verified, not merely written.** Two services sharing a secret otherwise accept each
  other's tokens, which is rarely what either of them intended.
- **`exp` is required.** A token with no expiry claim is valid forever, and PyJWT accepts one
  quite happily unless it is told the claim must be there.

The one thing it does *not* do is decide what "now" is. `exp` and `iat` must be **present** but
neither is compared against anything here, and PyJWT's own checks are switched off deliberately:
they read the system clock, and `dexter.iam` decides expiry from the injected `Clock`. Two
authorities on time is not caution, it is a footgun — an application whose clock is deliberately
elsewhere would find its own service rejecting tokens it had just minted, and PyJWT reports a
future `iat` as a signature problem rather than a clock one, so the reason would not even be
legible. There is one authority, expiry is enforced in `tokens.py`, and this file guarantees
only that a token which never claimed to expire cannot get past it.

The file is `jwt_codec.py` rather than `jwt.py` for the usual dexter reason — a file is named
after the class it defines — and for one more: a module called `dexter.iam.jwt` sitting beside
code that imports the third-party `jwt` is a five-minute puzzle for every reader who meets it.
"""

from typing import Any

import jwt

from .errors import InvalidTokenError

REQUIRED_CLAIMS = ["exp", "iat", "iss", "sub"]
"""Claims a token must carry to be worth reading.

A list rather than a set because PyJWT's `require` option takes one, and passing a set silently
does nothing useful.
"""


class JwtCodec:
    """Turns a claim set into a signed token, and back.

    Slotted: one per container, used on the resolution path of every authenticated request.
    """

    __slots__ = ("_algorithm", "_issuer", "_secret")

    def __init__(self, *, secret: str, algorithm: str, issuer: str) -> None:
        """Record the key, the algorithm and the issuer."""
        self._secret = secret
        self._algorithm = algorithm
        self._issuer = issuer

    def encode(self, claims: dict[str, Any], /) -> str:
        """Sign `claims` and return the token.

        `iss` is written here rather than expected in `claims`, so one codec cannot be talked
        into minting a token attributed to somebody else.
        """
        return jwt.encode(
            {**claims, "iss": self._issuer},
            key=self._secret,
            algorithm=self._algorithm,
        )

    def decode(self, token: str, /) -> dict[str, Any]:
        """Verify `token`'s signature and issuer, and return its claims.

        **Expiry is not judged here** — see the module docstring. `exp` is required to be
        present, and comparing it to a moment in time is `TokenService`'s job, against the
        clock the application chose.

        Raises:
            InvalidTokenError: For every reason it cannot be read — a bad signature, a
                different issuer, a missing required claim, or text that is not a token at all.
                They are one error on purpose: telling a caller which of them it was tells an
                attacker which half of their forgery worked.
        """
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=self._secret,
                # A whitelist of exactly the algorithm we sign with. Passing the token's own
                # header here is the `alg: none` forgery.
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={
                    "require": REQUIRED_CLAIMS,
                    "verify_iss": True,
                    # One authority on time, and it is not this one. Both are switched off,
                    # not just `exp`: PyJWT refuses a token whose `iat` is in the future, so
                    # leaving that on would reject every token minted by an application whose
                    # clock runs ahead of this machine's — the same disagreement, arriving as
                    # a signature error rather than an expiry one.
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidTokenError as error:
            raise InvalidTokenError("the token could not be verified.") from error
        return claims

    def __repr__(self) -> str:
        return f"JwtCodec(algorithm={self._algorithm!r}, issuer={self._issuer!r})"
