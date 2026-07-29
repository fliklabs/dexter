"""Composing an application from modules.

A **module** is one capability of a service — a domain, its handlers, its routes, the services
they need — registered by one function::

    def use_orders(builder: ContainerBuilder) -> None:
        \"\"\"Everything the orders module contributes.\"\"\"
        builder.register(Orders).to(InMemoryOrders, scope=Scope.SINGLETON)
        register_command_handler(builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT)
        register_handler(builder, PlaceOrderApi, HttpExposure(...), scope=Scope.TRANSIENT)

An **application** is a list of them::

    MODULES = (use_catalogue, use_orders)


    def build_container() -> Container:
        builder = ContainerBuilder()
        use_application(builder)
        for module in MODULES:
            register_module(builder, module)
        return builder.build()

**One list, however the service is run.** A web application hands the container to
`create_app`; a worker resolves the buses and processes work. They differ in what they *do*
with the modules, not in which modules they have — so nothing has to be kept in step, and a
module added for one is reachable from the other by construction.

**Modules do not import each other.** One that needs what another provides asks the container
for it by type. That is what makes registration order irrelevant, and what lets a module be
removed by deleting a line.
"""

from .errors import ApplicationError as ApplicationError
from .errors import ApplicationNotWiredError as ApplicationNotWiredError
from .errors import DuplicateModuleError as DuplicateModuleError
from .errors import InvalidModuleError as InvalidModuleError
from .models import Module as Module
from .models import describe_module as describe_module
from .registry import ModuleRegistry as ModuleRegistry
from .use import register_module as register_module
from .use import use_application as use_application
