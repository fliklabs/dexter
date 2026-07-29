"""What this service is made of. This is the file to read first.

Two lists and one function. Adding a capability is writing a module under `modules/` and adding
a line to `MODULES` — there is nowhere else to remember, because there is nowhere else that
knows what a module is.

**One list, whichever way the service runs.** `./dx serve` hands the container to `create_app`
and binds a socket; `python -m examples.storefront` resolves the buses and processes work
directly. They differ in what they *do* with the modules, never in which modules they have —
so a capability added for one is reachable from the other by construction, and there is no
second list to keep in step.
"""

from dexter.application import register_module, use_application
from dexter.dependency_injection import Container, ContainerBuilder

from .modules.catalogue.use import use_catalogue
from .modules.orders.use import use_orders

MODULES = (use_catalogue, use_orders)
"""Every module this service is made of, in registration order.

Order is not significant. Modules reach each other through contracts resolved from the
container, so `orders` can price against `catalogue` whichever way round these appear — try
swapping them.
"""


def build_container() -> Container:
    """Wire the service and return a container ready to use."""
    builder = ContainerBuilder()

    # Registers the CQRS registries and buses, the API registries, and the module registry —
    # once, before any module. A module never calls `use_cqrs` or `use_api` itself; both bind
    # unconditionally, so the second module to try would fail on a duplicate registration.
    use_application(builder)

    for module in MODULES:
        register_module(builder, module)

    return builder.build()
