"""Assembling the request SES v2 wants.

Its own file because the shape is the one genuinely awkward thing about choosing v2, and it is
worth being able to read it without the client around it. A simple message is
`Content.Simple.Body.{Text,Html}.Data` — four levels, three of which exist only to leave room for
`Raw` and `Template` beside `Simple`. Getting a level wrong produces a validation error naming a
member rather than the mistake.

The guards live here too, for one reason: they are the conditions under which no request should
be built at all, so they belong beside the building rather than in the method that sends.
"""

from collections.abc import Mapping, Sequence
from typing import Any


def build(  # noqa: PLR0913 - it mirrors `send_email`, which is where the count is argued
    *,
    sender: str,
    to: Sequence[str],
    subject: str,
    text: str | None,
    html: str | None,
    cc: Sequence[str],
    bcc: Sequence[str],
    reply_to: Sequence[str],
    configuration_set: str | None,
    tags: Mapping[str, str] | None,
) -> dict[str, Any]:
    """One `SendEmail` request.

    Raises:
        ValueError: If there is no recipient, or no body. Both are raised before anything is
            built, so the traceback points at the call that made the message rather than at a
            service error several layers away.
    """
    if not to:
        raise ValueError("send_email needs at least one recipient in `to`.")
    if text is None and html is None:
        raise ValueError("send_email needs a `text` body, an `html` body, or both.")

    # Both parts together is a multipart alternative, not a conflict: the recipient's client
    # picks. A message sent as HTML alone is the one that arrives blank in a text-only reader.
    body: dict[str, Any] = {}
    if text is not None:
        body["Text"] = {"Data": text}
    if html is not None:
        body["Html"] = {"Data": html}

    request: dict[str, Any] = {
        "FromEmailAddress": sender,
        "Destination": {
            "ToAddresses": list(to),
            "CcAddresses": list(cc),
            "BccAddresses": list(bcc),
        },
        "Content": {"Simple": {"Subject": {"Data": subject}, "Body": body}},
    }
    # Omitted rather than sent empty. An empty `ReplyToAddresses` is not the same thing to the
    # service as an absent one, and neither is an empty `EmailTags`.
    if reply_to:
        request["ReplyToAddresses"] = list(reply_to)
    if configuration_set is not None:
        request["ConfigurationSetName"] = configuration_set
    if tags:
        request["EmailTags"] = [
            {"Name": name, "Value": value} for name, value in tags.items()
        ]
    return request
