"""Exceptions raised by the IAM module.

Two conventions carry over from the rest of dexter. `args` stays a short one-line message, so
`pytest.raises(match=...)` and log lines remain readable. And a wiring mistake is reported
while wiring: everything under `IamRegistrationError` is raised before the container is built.

**A failure message never echoes the credential that caused it.** itamoo's equivalent renders
the whole rejected token into the error text, which then reaches every log line and traceback
that failure passes through — an access token is a bearer credential, and one in a log is one
that has been disclosed. The messages here say what was wrong, never what was presented.

**There is no `IamGroupError`.** One request has one outcome, and one code has one verdict.
"""

from dexter.commons import DexterError


class IamError(DexterError):
    """Base class for every IAM failure.

    Consumers can catch this to cover wiring, tokens and codes at once. `dexter.api`'s error
    map walks the MRO, so `register_error(builder, IamError, status=...)` would cover the whole
    subtree — which is exactly what you do **not** want here, because these mean different
    things to a caller. `use_authentication` maps the four that mean 401 individually.
    """


# ── registration ─────────────────────────────────────────────────────


class IamRegistrationError(IamError):
    """Authentication could not be wired. Raised before the container is built."""


class IamNotWiredError(IamRegistrationError):
    """`use_authentication` was never called on this builder.

    The registry `require_authentication` writes into is created by `use_authentication`, so it
    has to run first. Raised instead of the container's own "not registered as an instance"
    message, which names an internal type rather than the call the reader is missing.
    """


class DuplicateAuthenticationRuleError(IamRegistrationError):
    """The same handler was given an authentication rule twice.

    A second call is always a mistake: either it repeats the first, or the two disagree and
    which one wins would depend on the order unrelated wiring ran in.
    """


# ── tokens ───────────────────────────────────────────────────────────


class TokenError(IamError):
    """Base for every failure to read a token a caller presented."""


class InvalidTokenError(TokenError):
    """The token is not a token this application issued.

    A bad signature, a malformed structure, a claim that will not parse, or an issuer that is
    not ours. They are deliberately one error: telling a caller *which* of those it was tells
    an attacker which half of their forgery worked.
    """


class ExpiredTokenError(TokenError):
    """The token was ours, and its lifetime has passed.

    Distinct from `InvalidTokenError` because the client's response differs: an expired access
    token means refresh, and an invalid one means log in again.
    """


class WrongTokenKindError(TokenError):
    """A token of one kind was presented where another was required.

    Presenting a refresh token as a bearer credential is the case that matters: a refresh token
    outlives an access token by design, so accepting one as an access token silently grants the
    longer lifetime to every request. itamoo's token manager does not check this.
    """


class NotAuthenticatedError(IamError):
    """The route requires a caller, and the request named none.

    Raised by the middleware rather than returned, because a middleware spanning many handlers
    that *returns* has its value serialised through the refused route's response model.
    """


# ── magic codes ──────────────────────────────────────────────────────


class MagicCodeError(IamError):
    """Base for every failure of the magic-code exchange."""


class MagicCodeThrottledError(MagicCodeError):
    """A code was requested again too soon after the last one.

    The guard is on *issuing*, not on verifying, and it exists because the cost of a code is
    borne by whoever owns the address it is sent to.
    """


class MagicCodeMismatchError(MagicCodeError):
    """The code presented is not the code that was issued.

    Carries no hint of the expected value, and the attempt has been counted.
    """


class MagicCodeExpiredError(MagicCodeError):
    """There was a code for this key, and its lifetime has passed.

    The code is consumed when this is raised: an expired code is never verifiable again, so
    leaving it in the store would only be a row waiting to be swept.
    """


class NoMagicCodeError(MagicCodeError):
    """No code is outstanding for this key.

    Never distinguish this from a mismatch in what you tell the *caller* — the difference says
    whether an address has an account. It is a separate exception so an application can decide
    that for itself.
    """


class TooManyAttemptsError(MagicCodeError):
    """The code has been guessed at too many times, and has been consumed.

    Six digits is a million possibilities and an attacker needs only to be lucky once, so the
    attempt count is what makes a short code safe. Reaching the limit destroys the code rather
    than locking it: a lock is a denial-of-service handle on someone else's address.
    """
