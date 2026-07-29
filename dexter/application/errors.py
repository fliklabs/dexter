"""Exceptions raised while composing an application.

Every one of them is raised while wiring, before the container is built — which is the whole
point of this module. Composition mistakes are the cheapest kind to catch and the most
expensive kind to discover at runtime, because a service that starts and then cannot serve one
route looks like a bug in the route.
"""

from dexter.commons import DexterError


class ApplicationError(DexterError):
    """Base class for every failure composing an application."""


class ApplicationNotWiredError(ApplicationError):
    """`use_application` was never called on this builder.

    The registry a module registers into is created by `use_application`, so it has to run
    first. Raised instead of the container's own "not registered as an instance" message,
    which names an internal type rather than the call the reader is missing.
    """


class DuplicateModuleError(ApplicationError):
    """The module is already part of this application.

    Registering one twice would run its registrations twice, and the second run would fail
    somewhere inside it — on a duplicate handler, or a duplicate binding — reporting whichever
    guard happened to trip first rather than the mistake actually made.
    """


class InvalidModuleError(ApplicationError):
    """The module cannot be registered.

    A module is a callable taking the builder. Anything else — a class, a list of
    registrations, an already-built container — is a different idea that would have to be
    executed differently, and guessing which is not this module's job.
    """
