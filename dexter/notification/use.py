"""Wiring: how a notifier is bound into a container.

**There is no bare `use_notification`.** The core of this module is contracts and one test
double; it owns no registry, so a topology switch that registered nothing would exist only to
look symmetrical with the other modules. What it has instead is one `use_*` per engine, which
is AGENTS.md's rule read literally — *an alternative topology is a second `use_*` function,
never a flag*:

    from dexter.notification import use_recording_notification
    from dexter.notification.resend import register_resend_config, use_resend_notification

    use_recording_notification(builder)                       # tests, and local development
    use_resend_notification(builder)                          # a real provider
    register_resend_config(builder, ResendConfig(...))

Exactly one of them is called. Calling two binds `EmailNotifier` twice, and the container
refuses that with `DuplicateRegistrationError` — which is the right failure, because the
alternative is an application that silently sends real mail in its test suite.

`use_recording_notification` lives here rather than beside a provider because it *is* the
no-provider case, and because it must stay importable without any of them.
"""

from dexter.dependency_injection import ContainerBuilder

from .models import EmailNotifier
from .recording import RecordingEmailNotifier


def use_recording_notification(builder: ContainerBuilder) -> None:
    """Bind `EmailNotifier` to a notifier that records messages and sends none.

    `Scope.SINGLETON`, so everything in the application writes to the same recording and a
    test can read it back from the container after the request that produced it has finished.
    A transient notifier would hand each caller a fresh, empty list — which is the one
    behaviour that makes this useless.

    Both keys are bound to **the same object**, by instance rather than by provider:
    `EmailNotifier` for the code under test, which should name the contract, and
    `RecordingEmailNotifier` for the test itself, which needs `sent`. Two `to(...)` bindings
    would produce two singletons — one recording the messages and the other being read — which
    fails in the most confusing way available: an empty list and a passing send.
    """
    recorder = RecordingEmailNotifier()
    builder.register(RecordingEmailNotifier).to_instance(recorder)
    builder.register(EmailNotifier).to_instance(recorder)
