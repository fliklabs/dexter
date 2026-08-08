"""Fanning a message out to a topic, or sending one to a phone.

**`send_sms` defaults to transactional, and that is a correction rather than a preference.** SNS
takes the SMS type from an account-level default when the caller sets none, and that default is
`Promotional` unless somebody changed it. A promotional message is cheaper, deprioritised, and
subject to carrier filtering a one-time code does not survive — so a login that works in
development and silently fails for some users in production is the shape of the bug. dexter ships
`dexter.iam`, whose magic codes are exactly this traffic, so the safe default is the right one
here.
"""

from .client import SnsClient as SnsClient
