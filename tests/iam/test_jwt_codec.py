"""The JWT primitive: what it signs, and everything it refuses to read.

Most of this file is refusals, which is the right shape for the one piece of cryptography in
the library. Each guard here corresponds to a forgery a naive wrapper accepts.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from dexter.iam import InvalidTokenError, JwtCodec

from .conftest import EPOCH, ISSUER, SECRET


def make_codec(
    *, secret: str = SECRET, algorithm: str = "HS256", issuer: str = ISSUER
) -> JwtCodec:
    """A codec every argument of which a test may replace."""
    return JwtCodec(secret=secret, algorithm=algorithm, issuer=issuer)


def make_claims(**overrides: object) -> dict[str, object]:
    """A complete claim set, an hour from expiring."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "someone@example.com",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return claims


def payload_of(token: str) -> dict[str, object]:
    """The claims inside a token, read without verifying anything."""
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    decoded: dict[str, object] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


class TestRoundTrip:
    def test_reads_back_what_it_wrote(self) -> None:
        codec = make_codec()
        claims = make_claims(sub="a@b.com")

        read = codec.decode(codec.encode(claims))

        assert read["sub"] == "a@b.com"
        assert read["exp"] == claims["exp"]

    def test_writes_the_issuer_itself(self) -> None:
        """A codec cannot be talked into minting a token attributed to somebody else."""
        token = make_codec().encode(make_claims(iss="somebody-else"))

        assert payload_of(token)["iss"] == ISSUER

    def test_puts_claims_at_the_top_level(self) -> None:
        """Not nested under a wrapper key, so any JWT tool can read the token."""
        token = make_codec().encode(make_claims(sub="a@b.com"))

        payload = payload_of(token)

        assert payload["sub"] == "a@b.com"
        assert "data" not in payload


class TestRefusal:
    def test_refuses_a_token_signed_with_another_secret(self) -> None:
        other = make_codec(secret="a-completely-different-key-of-sufficient-length")
        token = other.encode(make_claims())

        with pytest.raises(InvalidTokenError):
            make_codec().decode(token)

    def test_refuses_a_tampered_payload(self) -> None:
        token = make_codec().encode(make_claims(sub="mallory@example.com"))
        header, _payload, signature = token.split(".")
        forged = base64.urlsafe_b64encode(
            json.dumps({**payload_of(token), "sub": "admin@example.com"}).encode()
        ).rstrip(b"=")

        with pytest.raises(InvalidTokenError):
            make_codec().decode(f"{header}.{forged.decode()}.{signature}")

    def test_refuses_a_token_from_another_issuer(self) -> None:
        """Two services sharing a secret must not accept each other's tokens."""
        other = make_codec(issuer="another-service")
        token = other.encode(make_claims())

        with pytest.raises(InvalidTokenError):
            make_codec().decode(token)

    def test_refuses_a_token_with_no_expiry(self) -> None:
        """A token without `exp` is valid forever, and PyJWT accepts one unless told not to."""
        claims = make_claims()
        del claims["exp"]
        token = jwt.encode({**claims, "iss": ISSUER}, key=SECRET, algorithm="HS256")

        with pytest.raises(InvalidTokenError):
            make_codec().decode(token)

    def test_refuses_a_token_with_no_subject(self) -> None:
        claims = make_claims()
        del claims["sub"]
        token = jwt.encode({**claims, "iss": ISSUER}, key=SECRET, algorithm="HS256")

        with pytest.raises(InvalidTokenError):
            make_codec().decode(token)

    def test_refuses_an_unsigned_token(self) -> None:
        """`alg: none` is the classic JWT forgery, and it must never be reachable."""
        token = jwt.encode({**make_claims(), "iss": ISSUER}, key="", algorithm="none")

        with pytest.raises(InvalidTokenError):
            make_codec().decode(token)

    def test_refuses_a_token_signed_with_a_different_algorithm(self) -> None:
        """Same key, stronger algorithm, and it is still refused.

        The decoder is given a whitelist of exactly the algorithm it signs with, so the token's
        own `alg` header never chooses. The key is long enough for `HS512` here so that what is
        under test is the algorithm check rather than a key-length warning.
        """
        long_enough = SECRET * 2
        token = jwt.encode(
            {**make_claims(), "iss": ISSUER}, key=long_enough, algorithm="HS512"
        )

        with pytest.raises(InvalidTokenError):
            make_codec(secret=long_enough).decode(token)

    def test_refuses_text_that_is_not_a_token(self) -> None:
        with pytest.raises(InvalidTokenError):
            make_codec().decode("not-a-token")


class TestExpiryIsSomebodyElsesJob:
    """One authority on time, and it is `TokenService` with the application's own clock."""

    def test_requires_an_expiry_claim_but_does_not_judge_it(self) -> None:
        long_dead = datetime.now(UTC) - timedelta(days=365)
        token = make_codec().encode(
            make_claims(
                iat=int(long_dead.timestamp()),
                exp=int((long_dead + timedelta(minutes=1)).timestamp()),
            )
        )

        read = make_codec().decode(token)

        assert read["sub"] == "someone@example.com"

    def test_a_token_dated_in_the_future_is_read_the_same_way(self) -> None:
        """A frozen clock in either direction has to round-trip, or nothing is testable."""
        ahead = datetime.now(UTC) + timedelta(days=365)
        token = make_codec().encode(
            make_claims(iat=int(ahead.timestamp()), exp=int(ahead.timestamp()))
        )

        assert make_codec().decode(token)["sub"] == "someone@example.com"


class TestReadability:
    def test_repr_names_the_algorithm_and_issuer_but_never_the_secret(self) -> None:
        rendered = repr(make_codec())

        assert "HS256" in rendered
        assert ISSUER in rendered
        assert SECRET not in rendered

    def test_the_epoch_fixture_is_aware(self) -> None:
        """A naive datetime compares unpredictably, and would fail exactly at expiry."""
        assert EPOCH.tzinfo is not None
