"""What a module is.

A module is one capability of an application — its domain, its handlers, its routes, the
services they need — and it is registered by calling a function. There is no `Module` class to
inherit and no manifest to build, because there is nothing a richer type would carry that the
function does not already have: its name is `__name__`, its purpose is `__doc__`, and what it
contributes is the `register_*` calls in its body, in the order it makes them.

That is deliberate rather than minimal. The alternative — a bundle object that *collects*
declarations now for something else to *execute* later — separates the mistake from its report:
a malformed handler is discovered while flattening six lists in a composition root that has
never heard of the module it came from. Running the function immediately means a module's
failure is raised inside that module's own call.
"""

from collections.abc import Callable

from dexter.dependency_injection import ContainerBuilder

type Module = Callable[[ContainerBuilder], None]
"""One capability of an application, registered by calling it with the builder.

By convention it is named `use_<module>` and lives in that module's `use.py`, matching the
shape every dexter module uses for its own wiring. It calls `register_*` and binds its own
services; it never calls `use_cqrs` or `use_api`, which belong to the application.
"""


def describe_module(module: Module) -> str:
    """Render a module as the name a reader would recognise.

    Falls back to `repr` for something without a name — a `functools.partial`, say — so an
    error message about an unusable module can still say which one.
    """
    return getattr(module, "__name__", None) or repr(module)
