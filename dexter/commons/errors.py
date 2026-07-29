"""The root of dexter's exception hierarchy."""

from collections.abc import Sequence
from typing import Self


class DexterError(Exception):
    """Base class for every exception raised by dexter.

    Each module defines its own subclass tree rooted here, so that consumers can
    catch everything from dexter with a single ``except DexterError``.
    """


class DexterGroupError(DexterError, ExceptionGroup[Exception]):
    """Base for a dexter failure that is genuinely plural.

    Independent work — several event handlers, several instances being disposed — has no
    privileged first failure, so reporting one and discarding the rest loses information the
    caller needs. Handle the arms with ``except*``, or catch the whole group as a
    ``DexterError``.

    Lives here rather than in a module because two modules now need the same machinery: an
    ``ExceptionGroup`` subclass has to construct through ``__new__`` and has to override
    ``derive``, and getting either wrong is silent.
    """

    def __new__(cls, message: str, exceptions: tuple[Exception, ...]) -> Self:
        """Build the group; ``BaseExceptionGroup`` constructs through ``__new__``."""
        return super().__new__(cls, message, exceptions)

    # The supertype declares `derive` as an overloaded generic returning
    # `ExceptionGroup[_ExceptionT]`, which no fixed `ExceptionGroup[Exception]` subclass can
    # satisfy: narrowing the element type is precisely what it cannot do.
    def derive(self, excs: Sequence[Exception], /) -> Self:  # type: ignore[override]
        """Keep the concrete class when ``except*`` splits the group.

        Without this, splitting produces a plain ``ExceptionGroup`` and the unhandled
        remainder stops being catchable as a ``DexterError``.
        """
        return type(self)(self.args[0], tuple(excs))
