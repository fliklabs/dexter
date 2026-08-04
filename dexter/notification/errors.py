"""Exceptions raised by the notification module.

The same convention as the rest of dexter: `args` stays a short one-line message, so
`pytest.raises(match=...)` and log lines remain readable.

**There is no registration error tree here, and that is not an omission.** The other modules
have one because they own a registry that wiring can get wrong. This module owns none — it is
two contracts and one test double — so the only thing that can fail is sending, and the only
wiring mistake possible is binding two engines, which the container itself refuses with
`DuplicateRegistrationError`. Adding `NotificationNotWiredError` to look symmetrical with
`dexter.api` would be an exception nothing could ever raise.

**There is no `NotificationGroupError` either.** AGENTS.md prescribes one for failures that are
genuinely plural, and this module has none: sending to several recipients is one request to one
provider, with one outcome.
"""

from dexter.commons import DexterError


class NotificationError(DexterError):
    """Base class for every notification failure."""


class DeliveryError(NotificationError):
    """A message could not be delivered.

    Raised for anything the provider refused or could not be asked: a rejected request, a
    transport failure, an answer that did not carry the identifier it promised. The message
    names the provider and what it said, because that is the only thing a reader can act on.

    **It does not distinguish "not sent" from "sent but unconfirmed"**, because the provider
    often cannot either. A caller retrying on this should assume the message may already have
    gone out.
    """
