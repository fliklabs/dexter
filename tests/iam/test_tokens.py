"""Minting a pair and reading it back, including the two kinds not being interchangeable."""

import base64
import json
from datetime import timedelta
from typing import Any

import pytest

from dexter.iam import (
    Claim,
    ExpiredTokenError,
    InvalidTokenError,
    JwtCodec,
    Principal,
    TokenKind,
    TokenPolicy,
    TokenService,
    WrongTokenKindError,
    describe_token_kind,
)

from .conftest import (
    EPOCH,
    ISSUER,
    SECRET,
    FrozenClock,
    make_token_policy,
    make_tokens,
)


def payload_of(token: str) -> dict[str, object]:
    """The claims inside a token, read without verifying anything."""
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    decoded: dict[str, object] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


class TestMinting:
    def test_mints_both_tokens_with_their_expiries(self) -> None:
        tokens = make_tokens()

        pair = tokens.mint(Principal(subject="a@b.com"))

        assert pair.access_token_expires_at == EPOCH + timedelta(minutes=15)
        assert pair.refresh_token_expires_at == EPOCH + timedelta(days=30)
        assert pair.access_token != pair.refresh_token

    def test_both_tokens_are_issued_at_one_instant(self) -> None:
        """Two reads of the clock could straddle a second and disagree."""
        pair = make_tokens().mint(Principal(subject="a@b.com"))

        assert (
            payload_of(pair.access_token)["iat"]
            == (payload_of(pair.refresh_token)["iat"])
        )

    def test_gives_every_token_a_unique_id(self) -> None:
        """Nothing reads `jti` today; a revocation store added later would key on it."""
        tokens = make_tokens()
        principal = Principal(subject="a@b.com")

        first = payload_of(tokens.mint(principal).access_token)["jti"]
        second = payload_of(tokens.mint(principal).access_token)["jti"]

        assert first != second

    def test_mints_one_access_token_and_says_when_it_dies(self) -> None:
        tokens = make_tokens(access_ttl=timedelta(minutes=5))

        token, expires_at = tokens.mint_access(Principal(subject="a@b.com"))

        assert expires_at == EPOCH + timedelta(minutes=5)
        assert tokens.verify_access(token).subject == "a@b.com"

    def test_carries_claims_through_the_token(self) -> None:
        tokens = make_tokens()

        token = tokens.mint(Principal.of("a@b.com", role="admin")).access_token

        assert tokens.verify_access(token).claim("role") == "admin"

    def test_a_principal_with_no_claims_writes_no_claims_field(self) -> None:
        token = make_tokens().mint(Principal(subject="a@b.com")).access_token

        assert "claims" not in payload_of(token)


class TestVerification:
    def test_reads_the_subject_back(self) -> None:
        tokens = make_tokens()

        pair = tokens.mint(Principal(subject="a@b.com"))

        assert tokens.verify_access(pair.access_token).subject == "a@b.com"
        assert tokens.verify_refresh(pair.refresh_token).subject == "a@b.com"

    def test_refuses_a_refresh_token_as_a_bearer_credential(self) -> None:
        """The one that matters: a refresh token outlives an access token by design."""
        tokens = make_tokens()
        pair = tokens.mint(Principal(subject="a@b.com"))

        with pytest.raises(WrongTokenKindError, match="access"):
            tokens.verify_access(pair.refresh_token)

    def test_refuses_an_access_token_where_a_refresh_token_is_required(self) -> None:
        tokens = make_tokens()
        pair = tokens.mint(Principal(subject="a@b.com"))

        with pytest.raises(WrongTokenKindError, match="refresh"):
            tokens.verify_refresh(pair.access_token)

    def test_refuses_a_token_minted_for_another_issuer(self) -> None:
        theirs = TokenService(
            make_token_policy(issuer="another-service"), FrozenClock()
        )
        token = theirs.mint(Principal(subject="a@b.com")).access_token

        with pytest.raises(InvalidTokenError):
            make_tokens().verify_access(token)

    def test_refuses_a_token_minted_with_another_secret(self) -> None:
        theirs = TokenService(
            make_token_policy(secret="another-key-that-is-quite-long-enough-here"),
            FrozenClock(),
        )
        token = theirs.mint(Principal(subject="a@b.com")).access_token

        with pytest.raises(InvalidTokenError):
            make_tokens().verify_access(token)


class TestExpiry:
    def test_refuses_an_access_token_once_its_lifetime_has_passed(self) -> None:
        clock = FrozenClock()
        tokens = make_tokens(clock, access_ttl=timedelta(minutes=15))
        token = tokens.mint(Principal(subject="a@b.com")).access_token

        clock.advance(timedelta(minutes=16))

        with pytest.raises(ExpiredTokenError):
            tokens.verify_access(token)

    def test_expiry_is_judged_against_the_injected_clock(self) -> None:
        """A frozen clock in the past accepts a token the system clock would have buried."""
        clock = FrozenClock()
        tokens = make_tokens(clock)
        token = tokens.mint(Principal(subject="a@b.com")).access_token

        clock.advance(timedelta(minutes=14))

        assert tokens.verify_access(token).subject == "a@b.com"

    def test_a_refresh_token_outlives_an_access_token(self) -> None:
        clock = FrozenClock()
        tokens = make_tokens(clock)
        pair = tokens.mint(Principal(subject="a@b.com"))

        clock.advance(timedelta(hours=1))

        with pytest.raises(ExpiredTokenError):
            tokens.verify_access(pair.access_token)
        assert tokens.verify_refresh(pair.refresh_token).subject == "a@b.com"


def sign(**claims: object) -> str:
    """Sign an arbitrary claim set with the tests' own key, through the public codec.

    This is how a token that is genuinely *ours* — correct signature, correct issuer — but
    carries a shape `TokenService` does not expect gets built. That is not a hypothetical: it
    is what a token minted by a previous release looks like after a rollback.
    """
    return JwtCodec(secret=SECRET, algorithm="HS256", issuer=ISSUER).encode(
        dict(claims)
    )


def valid_claims(**overrides: object) -> dict[str, object]:
    """A claim set `TokenService` would accept, before a test spoils one field."""
    claims: dict[str, object] = {
        "sub": "a@b.com",
        "kind": str(TokenKind.ACCESS),
        "iat": int(EPOCH.timestamp()),
        "exp": int((EPOCH + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return claims


class TestMalformedClaims:
    """A signed token is ours, but "ours" includes a version of us that wrote another shape."""

    def test_refuses_a_token_whose_claims_are_not_a_list(self) -> None:
        token = sign(**valid_claims(claims={"role": "admin"}))

        with pytest.raises(InvalidTokenError, match="not a list"):
            make_tokens().verify_access(token)

    def test_refuses_a_claim_that_is_not_a_pair(self) -> None:
        token = sign(**valid_claims(claims=[["role"]]))

        with pytest.raises(InvalidTokenError, match="name and a value"):
            make_tokens().verify_access(token)

    def test_refuses_a_claim_that_is_not_two_strings(self) -> None:
        token = sign(**valid_claims(claims=[["role", 7]]))

        with pytest.raises(InvalidTokenError, match="pair of strings"):
            make_tokens().verify_access(token)

    def test_refuses_a_token_with_an_empty_subject(self) -> None:
        token = sign(**valid_claims(sub=""))

        with pytest.raises(InvalidTokenError, match="no subject"):
            make_tokens().verify_access(token)

    def test_refuses_a_token_whose_expiry_is_not_a_number(self) -> None:
        """Reachable precisely because PyJWT is told not to look at `exp`."""
        token = sign(**valid_claims(exp="the day after tomorrow"))

        with pytest.raises(InvalidTokenError, match="not a number"):
            make_tokens().verify_access(token)

    def test_refuses_a_token_whose_expiry_is_a_boolean(self) -> None:
        """`True` is an `int` in Python, and would otherwise be read as one second past 1970."""
        token = sign(**valid_claims(exp=True))

        with pytest.raises(InvalidTokenError, match="not a number"):
            make_tokens().verify_access(token)

    def test_refuses_a_token_whose_expiry_is_out_of_range(self) -> None:
        token = sign(**valid_claims(exp=10**20))

        with pytest.raises(InvalidTokenError, match="out of range"):
            make_tokens().verify_access(token)

    def test_refuses_a_token_with_no_kind(self) -> None:
        claims = valid_claims()
        del claims["kind"]

        with pytest.raises(WrongTokenKindError):
            make_tokens().verify_access(sign(**claims))

    def test_accepts_a_token_carrying_no_claims_field(self) -> None:
        assert make_tokens().verify_access(sign(**valid_claims())).claims == ()


class TestPolicy:
    def test_refuses_an_algorithm_outside_the_symmetric_family(self) -> None:
        with pytest.raises(ValueError, match="not supported"):
            make_token_policy(algorithm="RS256")

    def test_refuses_an_unsigned_algorithm(self) -> None:
        with pytest.raises(ValueError, match="not supported"):
            make_token_policy(algorithm="none")

    def test_refuses_a_secret_too_short_for_the_algorithm(self) -> None:
        """32 characters is ample for HS256 and half of what HS512 needs."""
        with pytest.raises(ValueError, match="needs at least 64"):
            make_token_policy(algorithm="HS512")

    def test_accepts_a_secret_long_enough_for_the_algorithm(self) -> None:
        policy = make_token_policy(algorithm="HS512", secret=SECRET * 2)

        assert policy.algorithm == "HS512"

    def test_refuses_a_secret_shorter_than_the_floor(self) -> None:
        with pytest.raises(ValueError, match="at least 32"):
            make_token_policy(secret="too-short")

    def test_refuses_a_lifetime_that_has_already_passed(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            make_token_policy(access_ttl=timedelta(seconds=-1))

    def test_refuses_an_unknown_field(self) -> None:
        """A typo in a field name is a policy that silently does nothing. `extra="forbid"`."""
        misspelled: Any = {
            "secret": SECRET,
            "issuer": ISSUER,
            "acess_ttl": timedelta(minutes=1),
        }

        with pytest.raises(ValueError, match="Extra inputs"):
            TokenPolicy(**misspelled)


class TestPrincipal:
    def test_of_builds_claims_from_keywords(self) -> None:
        principal = Principal.of("a@b.com", role="admin", team="ops")

        assert principal.claims == (
            Claim(name="role", value="admin"),
            Claim(name="team", value="ops"),
        )

    def test_claim_returns_none_for_a_name_that_is_not_there(self) -> None:
        assert Principal.of("a@b.com").claim("role") is None

    def test_refuses_an_empty_subject(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            Principal(subject="")

    def test_is_hashable_so_it_can_be_put_in_a_set(self) -> None:
        """Frozen is only shallow, so a list field would silently break this."""
        assert len({Principal.of("a@b.com"), Principal.of("a@b.com")}) == 1


class TestRendering:
    def test_describes_a_kind_as_the_symbol_a_reader_would_type(self) -> None:
        assert describe_token_kind(TokenKind.ACCESS) == "TokenKind.ACCESS"

    def test_repr_never_shows_the_secret(self) -> None:
        assert SECRET not in repr(make_tokens())
