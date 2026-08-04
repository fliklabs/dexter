"""Who a caller is: magic codes in, signed tokens out.

Two exchanges, and nothing else. A code is issued to an address and verified once::

    code = await codes.issue(email)  # returns it once; the store keeps a digest
    await notifier.send(Email(..., body=EmailBody.text(f"Your code is {code}.")))
    ...
    await codes.verify(email, presented)  # raises, or consumes the code
    pair = tokens.mint(Principal.of(email))

and a token is read back on the way in::

    principal = tokens.verify_access(bearer)  # raises, or says who

Wiring is the same two shapes every dexter module uses — what the module provides, then what
the application contributes::

    use_iam(builder)
    use_in_memory_magic_codes(builder)
    register_token_policy(builder, TokenPolicy(secret=..., issuer="plum"))
    register_magic_code_policy(builder, MagicCodePolicy(secret=...))

**The middleware lives in `dexter.iam.api`, not here.** Everything in this package is
transport-agnostic and importing it pulls in nothing else from dexter; the piece that turns an
`Authorization` header into a `Principal` is a package of its own, so that a worker application
can mint tokens without an API in its graph and so the boundary is a directory a test can walk.

**dexter has no opinion about who is allowed in.** There is no user table, no whitelist and no
registration flow — `issue` will make a code for any key it is given. Deciding whether an
address may log in is the application's, and it belongs *before* the call to `issue`, so that a
rejected address never costs a stored record or a sent message.

**What is deliberately absent:**

| | |
| --- | --- |
| Authorization | Nothing here answers "may they". This module answers "who are they" |
| Sessions | Refresh is stateless, so logout clears the client and nothing else. See `tokens.py` |
| Persistence | `InMemoryMagicCodeStore` is the only store, and it is one process's dictionary |
| Password login | A different exchange with different failure modes. It is not this one wearing a hat |
"""

from .clock import SystemClock as SystemClock
from .errors import (
    DuplicateAuthenticationRuleError as DuplicateAuthenticationRuleError,
)
from .errors import ExpiredTokenError as ExpiredTokenError
from .errors import IamError as IamError
from .errors import IamNotWiredError as IamNotWiredError
from .errors import IamRegistrationError as IamRegistrationError
from .errors import InvalidTokenError as InvalidTokenError
from .errors import MagicCodeError as MagicCodeError
from .errors import MagicCodeExpiredError as MagicCodeExpiredError
from .errors import MagicCodeMismatchError as MagicCodeMismatchError
from .errors import MagicCodeThrottledError as MagicCodeThrottledError
from .errors import NoMagicCodeError as NoMagicCodeError
from .errors import NotAuthenticatedError as NotAuthenticatedError
from .errors import TokenError as TokenError
from .errors import TooManyAttemptsError as TooManyAttemptsError
from .errors import WrongTokenKindError as WrongTokenKindError
from .jwt_codec import JwtCodec as JwtCodec
from .magic_code import MagicCodeService as MagicCodeService
from .models import DIGITS as DIGITS
from .models import HMAC_ALGORITHMS as HMAC_ALGORITHMS
from .models import Claim as Claim
from .models import Clock as Clock
from .models import MagicCode as MagicCode
from .models import MagicCodePolicy as MagicCodePolicy
from .models import MagicCodeStore as MagicCodeStore
from .models import Principal as Principal
from .models import TokenKind as TokenKind
from .models import TokenPair as TokenPair
from .models import TokenPolicy as TokenPolicy
from .models import describe_token_kind as describe_token_kind
from .stores import InMemoryMagicCodeStore as InMemoryMagicCodeStore
from .tokens import TokenService as TokenService
from .use import register_magic_code_policy as register_magic_code_policy
from .use import register_token_policy as register_token_policy
from .use import use_iam as use_iam
from .use import use_in_memory_magic_codes as use_in_memory_magic_codes
