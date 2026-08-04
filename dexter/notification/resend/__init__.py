"""Sending mail through Resend.

The only part of `dexter.notification` that knows a provider exists, and the only part that
imports an HTTP client. Importing this package is what pulls one in, which is why it is a
package of its own rather than a few files beside the rest: the boundary is a directory a test
can walk, and a second engine sits next to it with the same guarantee.

`httpx` is an **optional** dependency. Install it with the extra that names this engine::

    uv add "dexter[resend] @ git+https://github.com/fliklabs/dexter"

A consumer who never sends mail inherits nothing from this file.
"""

from .notifier import ENDPOINT as ENDPOINT
from .notifier import RESEND_FIELD as RESEND_FIELD
from .notifier import ResendConfig as ResendConfig
from .notifier import ResendEmailNotifier as ResendEmailNotifier
from .use import register_resend_config as register_resend_config
from .use import use_resend_notification as use_resend_notification
