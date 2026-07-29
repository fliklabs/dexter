"""Shared primitives used across dexter modules.

This package sits at the bottom of the dependency graph: it must never import from
another dexter module. Nothing is added here until at least two modules need it —
see AGENTS.md.
"""

from .errors import DexterError as DexterError
from .errors import DexterGroupError as DexterGroupError
from .type_utils import describe_type as describe_type
