"""Sending mail through SES.

**`sesv2`, not `ses`, and the choice is worth the paragraph.** The v1 API is in maintenance:
everything added since 2019 — configuration-set engagement metrics, the Virtual Deliverability
Manager, list and contact management, per-send tags — exists only in v2. More immediately, v2
folds Simple, Raw and Templated sending into one `Content` union on one `SendEmail`, so a
text-and-HTML message and a MIME message with an attachment are the same operation; v1 needs
three. The usual reason to stay on v1 is that migrating means changing an IAM policy, and it does
not: the action is `ses:SendEmail` for both.

What it costs is that the request shape — `Content.Simple.Body.{Text,Html}.Data` — is deeper than
v1's and easy to get wrong, and that every search result shows v1. That is a documentation cost
rather than a capability one, and it is why the assembly has its own file.

**Nothing here names another dexter module.** Plugging SES into `dexter.notification`'s
`EmailNotifier` contract is `dexter/notification/ses/`, beside the Resend engine and pointing the
same way. A worker sending a receipt through this client pulls in no notification module.
"""

from .client import SesClient as SesClient
