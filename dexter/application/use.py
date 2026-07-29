"""Wiring: how an application is composed from modules.

The same two shapes every dexter module uses, one level up. `use_application(builder)`
registers what an *application* needs — the buses, the API registry, and the module registry —
and takes no configuration. `register_module(builder, ...)` registers what the *service*
contributes, once per module.

    builder = ContainerBuilder()
    use_application(builder)
    for module in (use_catalogue, use_orders):
        register_module(builder, module)
    container = builder.build()

**A module never calls `use_cqrs` or `use_api`, and this is why the module exists.** Both bind
their registries unconditionally and `ContainerBuilder.register` refuses a repeat, so they can
be called exactly once per builder — the second module to wire its own would fail on a
duplicate registration, naming an internal registry rather than the mistake. Calling them here,
once, ahead of every module, means a module cannot make that mistake at all.
"""

from dexter.api import use_api
from dexter.commons import describe_type
from dexter.cqrs import use_cqrs
from dexter.dependency_injection import ContainerBuilder, InvalidRegistrationError

from .errors import ApplicationNotWiredError
from .models import Module
from .registry import ModuleRegistry


def use_application(builder: ContainerBuilder) -> None:
    """Register what every application needs. Call once, before any module.

    That is the three CQRS registries and their buses, the API exposure registry, pipeline and
    error map, and the registry of modules itself — bound as an instance so `register_module`
    can populate it while wiring, before the container is built.

    **The API registries are wired even for an application that serves no HTTP**, and that is
    deliberate. A module declares everything it offers; an application decides which of those
    surfaces to *expose*, not which to register. The alternative is a second topology that
    omits them, and then a module contributing a route cannot be registered into it — so every
    module offering both would have to be split in two, and the two halves kept in step by
    hand for every application that exists. Registering an exposure nobody serves costs one
    dictionary entry.

    Nothing here imports a web framework: `use_api` lives in `dexter.api`, and only
    `dexter.api.http` knows one exists.
    """
    use_cqrs(builder)
    use_api(builder)
    builder.register(ModuleRegistry).to_instance(ModuleRegistry())


def register_module(builder: ContainerBuilder, module: Module, /) -> None:
    """Add one module's registrations to the application.

    The module is recorded and then **run immediately**, rather than collected now and executed
    later. That is what keeps a failure attached to its cause: a malformed handler is raised
    inside the call to the module that declared it, with that module's frame on the traceback,
    instead of surfacing from a composition root that has never heard of it.

    It also means the `use_*`-before-`register_*` rule needs no enforcement here. Modules run
    after `use_application` because they run when this is called, and this cannot be called
    before it.

    Args:
        builder: The builder `use_application` was called on.
        module: A callable taking the builder — by convention `use_<module>` from that
            module's own `use.py`.
    """
    registry = _fetch(builder)
    registry.add(module)
    module(builder)


def _fetch(builder: ContainerBuilder) -> ModuleRegistry:
    """Fetch the module registry from the builder, or explain that wiring is missing."""
    try:
        return builder.resolve_instance(ModuleRegistry)
    except InvalidRegistrationError as error:
        raise ApplicationNotWiredError(
            f"{describe_type(ModuleRegistry)} is not registered, so there is nothing to "
            f"register into. Call `use_application(builder)` before registering modules."
        ) from error
