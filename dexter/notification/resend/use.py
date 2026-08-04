"""Wiring: how the Resend engine is bound into a container.

The same two shapes every dexter module uses. `use_resend_notification(builder)` says which
engine sends mail and takes no configuration; `register_resend_config(builder, config)` is what
the *application* contributes, because an API key is a value an application owns and dexter
reads no environment.

    use_resend_notification(builder)
    register_resend_config(builder, ResendConfig(api_key=settings.resend_api_key))

Order between the two does not matter — neither reads the other while wiring — but both must
happen before `build()`, and a missing config is reported by the container as `ResendConfig` not
being registered, which names exactly the call to add.
"""

from dexter.dependency_injection import ContainerBuilder, Scope

from ..models import EmailNotifier
from .notifier import ResendConfig, ResendEmailNotifier


def use_resend_notification(builder: ContainerBuilder) -> None:
    """Bind `EmailNotifier` to the Resend engine.

    Call once, and never alongside another engine's `use_*`: they bind the same key and the
    container refuses the second, which is the right failure. An application that wants to send
    nothing in its tests picks `use_recording_notification` *instead of* this.

    `Scope.SINGLETON`, because the notifier holds only its configuration and opens its
    connection per send — there is no per-request state to keep and nothing to dispose.

    Both keys are bound, and `EmailNotifier` resolves *through* the engine's own key rather
    than being built a second time from the same class — two bindings of one class produce two
    instances, and a notifier that later grows a connection pool would then have two of those.
    """
    builder.register(ResendEmailNotifier).to(ResendEmailNotifier, scope=Scope.SINGLETON)
    builder.register(EmailNotifier).to(_the_engine, scope=Scope.SINGLETON)


def _the_engine(notifier: ResendEmailNotifier) -> ResendEmailNotifier:
    """Resolve the engine that is already bound, so both keys name one object."""
    return notifier


def register_resend_config(builder: ContainerBuilder, config: ResendConfig, /) -> None:
    """Bind the credentials and endpoint the notifier sends with.

    Nothing is constructed here, so there is no `scope=` to choose: an existing object is
    inherently a single object. `scope=` is required on a `register_*` that binds a provider,
    and this one does not.
    """
    builder.register(ResendConfig).to_instance(config)
