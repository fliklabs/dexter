"""What an application is made of.

Populated while wiring and read-only in practice thereafter: `use_application` binds it as an
instance, `register_module` fills it, and the container is built afterwards. It holds the
modules in the order they were registered, which is the order their registrations ran.

Worth being clear about what it is *not*: it is not how modules find each other. A module that
needs something another module provides asks the container for it, by type, and neither module
mentions the other. Nothing here expresses a dependency, and nothing checks one — an
application missing a module it needs fails when that dependency is resolved, with the
container's own chain naming what was looked for and what led to it.
"""

from typing import Any

from .errors import DuplicateModuleError, InvalidModuleError
from .models import Module, describe_module


class ModuleRegistry:
    """Every module registered into one application, in registration order."""

    __slots__ = ("_modules",)

    def __init__(self) -> None:
        """Start with nothing registered."""
        self._modules: list[Module] = []

    def add(self, module: Module, /) -> None:
        """Record `module` as part of this application.

        Raises:
            InvalidModuleError: If it is not callable.
            DuplicateModuleError: If it is already registered.
        """
        candidate: Any = module
        if not callable(candidate):
            raise InvalidModuleError(
                f"{module!r} cannot be a module; a module is a function taking the builder."
            )
        if module in self._modules:
            raise DuplicateModuleError(
                f"{describe_module(module)} is already registered; its registrations would "
                f"run twice."
            )
        self._modules.append(module)

    def modules(self) -> tuple[Module, ...]:
        """Every module, in registration order."""
        return tuple(self._modules)

    def names(self) -> tuple[str, ...]:
        """Every module's name, in registration order.

        What an application prints when asked what it is made of, and what a test asserts on.
        """
        return tuple(describe_module(module) for module in self._modules)

    def __len__(self) -> int:
        """How many modules this application has."""
        return len(self._modules)

    def __repr__(self) -> str:
        return f"ModuleRegistry({', '.join(self.names())})"
