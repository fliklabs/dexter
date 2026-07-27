"""The root of dexter's exception hierarchy."""


class DexterError(Exception):
    """Base class for every exception raised by dexter.

    Each module defines its own subclass tree rooted here, so that consumers can
    catch everything from dexter with a single ``except DexterError``.
    """
