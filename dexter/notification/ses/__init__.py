"""The SES engine.

The second implementation of `EmailNotifier`, beside `dexter.notification.resend`, and the only
directory in this module that may import `dexter.aws`.
"""

from .notifier import SesEmailNotifier as SesEmailNotifier
from .use import use_ses_notification as use_ses_notification
