"""Exceptions raised by the dependency injection module."""

from dexter.commons import DexterError


class DependencyInjectionError(DexterError):
    """Base class for every dependency injection failure.

    Registration and resolution failures will each get their own subclass tree
    beneath this one when the container is implemented.
    """
