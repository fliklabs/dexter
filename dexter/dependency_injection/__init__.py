"""Dependency injection container.

Scaffolding only — the public API is not designed yet. The container, its
registration surface and its scope model land in a later change; only the module's
error root exists so far.
"""

from .errors import DependencyInjectionError as DependencyInjectionError
