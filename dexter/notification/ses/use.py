"""Wiring: how the SES engine is bound into a container.

One `use_*` and no `register_*`, which is the difference from the Resend engine and follows from
where the configuration lives. Resend needs an API key, so it has a config object to bind; SES
needs a region and an identity, and those belong to `dexter.aws`::

    use_aws(builder)
    register_aws_config(builder, AwsConfig(region="ap-southeast-2"))
    use_ses_notification(builder)

`use_aws` must run first, because this binds a notifier over the `SesClient` it registers. The
wrong order is reported by the container as `SesClient` not being registered, which names the
call to add — there is no registry here for a module error to improve on.
"""

from dexter.dependency_injection import ContainerBuilder, Scope

from ..models import EmailNotifier
from .notifier import SesEmailNotifier


def use_ses_notification(builder: ContainerBuilder) -> None:
    """Bind `EmailNotifier` to the SES engine.

    Call once, and never alongside another engine's `use_*`: they bind the same key and the
    container refuses the second, which is the right failure. An application that wants to send
    nothing in its tests picks `use_recording_notification` *instead of* this.

    `Scope.SINGLETON`, because the notifier holds only the AWS client — which is itself a
    singleton holding a connection pool — and has no per-request state.

    Both keys are bound, and `EmailNotifier` resolves *through* the engine's own key rather than
    being built a second time from the same class: two bindings of one class produce two
    instances, and a test that reads from one while the code under test sends through the other
    is a test that proves nothing.
    """
    builder.register(SesEmailNotifier).to(SesEmailNotifier, scope=Scope.SINGLETON)
    builder.register(EmailNotifier).to(_the_engine, scope=Scope.SINGLETON)


def _the_engine(notifier: SesEmailNotifier) -> SesEmailNotifier:
    """Resolve the engine that is already bound, so both keys name one object."""
    return notifier
