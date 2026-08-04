# AGENTS.md — `dexter.iam`

Answers **who is calling**. It does not answer **may they**, and it has no user table.

## Layout

| Path | Holds |
| --- | --- |
| `models.py` | `Principal`, `Claim`, `TokenPair`, `TokenKind`, the two policies, `MagicCode`, and the `MagicCodeStore` / `Clock` protocols |
| `errors.py` | The exception tree. Imports nothing else from this module |
| `clock.py` | `SystemClock` — **the one place `datetime.now` is called** |
| `jwt_codec.py` | `JwtCodec` — **the only file in dexter that imports `jwt`** |
| `tokens.py` | `TokenService`. Owns the claim shape; the codec owns the signing |
| `magic_code.py` | `MagicCodeService`. Six guards, each documented against the failure it closes |
| `stores.py` | `InMemoryMagicCodeStore` |
| `use.py` | `use_iam`, `use_in_memory_magic_codes`, the two `register_*_policy` |
| `api/` | The adapter — **the only directory that may import `dexter.api`** |

Both boundaries are enforced by `tests/iam/test_boundaries.py`, by AST walk *and* by a
subprocess check that importing the core leaves the adapter out. A negative check alone would
pass if the adapter were simply broken.

## Decisions that are not obvious from the code

**Refresh is stateless, and that means no revocation.** A refresh token is a self-contained JWT
with an `exp`; nothing is looked up, so logging out clears the client and the token stays valid
until it dies. Every token already carries a `jti`, which is the handle a session store or a
revocation list would key on — adding one is a `SessionStore` protocol and one lookup in
`verify_refresh`, and nothing minted today becomes unreadable.

**There is one authority on time and it is the injected `Clock`.** `JwtCodec` requires `exp` and
`iat` to be present and refuses to judge either; PyJWT's own checks are switched off. Two
authorities is not caution — an application whose clock is deliberately elsewhere would find its
own service rejecting tokens it had just minted, and PyJWT reports a future `iat` as a signature
problem, so the reason would not even be legible.

**`use_iam` leaves an application's own `Clock` alone.** The one conditional binding in the
module. The container refuses a second binding of a key, so an unconditional one would make the
clock the single thing here that could never be replaced.

**`MagicCodeStore` is bound by a separate `use_*`, deliberately.** Same reason: had `use_iam`
chosen one, no consumer could pick another.

**Two ways to ask who is calling, and the difference is the point.** `Principal` resolves to a
caller or raises, so declaring one *is* the statement that the code cannot run anonymously — and
because handler dependencies are built inside the request's error handling, that renders as a
401 rather than a 500. `Authentication` always resolves and may name nobody.

**Default open.** A handler nobody named is anonymous; `require_authentication` closes one. The
opposite default makes every public route carry a line of wiring. What keeps it safe is that
`AuthenticationRegistry.requirements()` lists every rule, so an application that wants
default-deny can assert over `ExposureRegistry.records()` that each handler was named.

**The middleware raises, never returns.** A middleware spanning many handlers that returns has
its value serialised through the refused route's response model — a 500 about response
validation instead of a 401. `use_authentication` maps the four token errors to 401 for it.

**A bad token is refused even on an open route.** Absence is an ordinary state; a token that
will not verify is a caller doing something wrong, usually a client that kept one past a key
rotation.

**No error message ever echoes the credential.** itamoo's equivalent renders the rejected token
into the error text, which then reaches every log line that failure passes through.

## What is deliberately absent

| | |
| --- | --- |
| Authorization | Answering "may they" is a separate module, and this one is a dependency of it |
| Sessions, refresh rotation | Follows from stateless refresh. See above |
| Persistence | One in-memory store. dexter ships no database anywhere |
| A user table, a whitelist | Who may log in is the application's decision, and belongs *before* `issue` |
| Password login | A different exchange with different failure modes, not this one wearing a hat |
| Asymmetric signing | Nothing here manages a key pair; `KEY_BYTES` is the whitelist |

## Measured against

`itamoo-app`'s `backend/i/iam` is the reference this generalises, and the differences are the
value. It compares codes with `!=`, stores them in plaintext, counts no attempts, leaves single
use to the caller, checks the code before the expiry, never verifies that a refresh token is not
being spent as an access token, and raises `AccessTokenExpiredError()` without its required
argument — a `TypeError` on the failure path. Each of those is a named test here.
