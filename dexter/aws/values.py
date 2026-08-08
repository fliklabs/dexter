"""A configuration value a component depends on, without depending on where it is kept.

This is the seam the rest of the module exists to serve. A repository needs a table name; a
client needs an API key. On a laptop those come from a settings object, and in a deployment they
come from the parameter store or from Secrets Manager — and the component holding one must not
be able to tell which, or the two environments run different code::

    class OrdersTableName(ValueSource, Protocol):
        \"\"\"The table orders live in.\"\"\"

    # locally: no AWS account, no credentials, no network
    builder.register(OrdersTableName).to_instance(StaticValue("orders-local"))

    # deployed
    register_parameter_value(builder, OrdersTableName,
                             name="/app/production/orders-table", scope=Scope.SINGLETON)

**The application declares the marker, and that is what makes the value injectable.** A `str`
cannot be a container key — every string value in the process would collide on it — so the
question "which string is this" has to be answered by a type. A one-line `Protocol` subclass in
the application's own vocabulary answers it, is greppable, and is what the container's
`UnregisteredDependencyError` names when the wiring is missing.

**A value, not a string, and the difference is dexter's lifetime rules.** Registering the `str`
itself would mean choosing between a `Scope.SINGLETON` binding — frozen for the life of the
process, so a rotation never lands — and `Scope.SCOPED`, which trips `CaptiveDependencyError`
for every singleton that depends on it. There is no third option: dexter's validator makes "one
instance, fresh value" inexpressible as a plain value. Behind an interface both are true at
once — the object is a singleton, the value has a lifetime. A `str` subclass would also print
itself in every traceback and every pydantic `ValidationError`, which for a password is the end
of the discussion.

**Strings only. There is no `ValueSource[T]`.** Every backing store is string-typed at the wire,
so a type parameter would be decoration over `str` in every implementation there is. Conversion
belongs where the schema is known, because that is the only place an error can say which field
was wrong: `int(await self._attempts.value())` inside a retry policy can name `attempts`, where
a generic source raises `invalid literal for int()` from a closure named nothing.
"""

from typing import Protocol


class ValueSource(Protocol):
    """Produces one configuration value, however and wherever it is stored.

    Asynchronous because an implementation may have to fetch it. `StaticValue` awaits nothing
    and returns immediately; that costs a coroutine and buys one interface instead of two.

    **There is no `refresh` parameter, and adding one later would be a breaking change** — every
    existing `async def value(self) -> str` would silently stop satisfying the protocol. So it is
    deliberate rather than deferred: staleness is bounded by the lifetime in `AwsConfig`, and an
    operator who cannot wait calls `invalidate` on the client. A caller who could classify a
    downstream failure as "this credential is stale" would be a caller that knows where its value
    came from, which is the one thing this interface exists to prevent.
    """

    async def value(self) -> str:
        """Return the value.

        Raises:
            DexterError: If it could not be obtained. The concrete type belongs to whichever
                implementation was bound — this protocol names none of them, which is the
                point.
        """
        ...


class StaticValue:
    """A value the application already has: from settings, from an environment variable, a test.

    The other half of the substitution. It reaches no network, needs no credentials, and is what
    a local run and a test suite bind so that the component under them is the same component.

    A plain slotted class rather than a frozen pydantic model, and the reason is the one thing a
    value holder has to get right: **`repr` must not disclose what it holds.** A pydantic model
    prints its fields in `repr` *and* in the `ValidationError` raised by an unrelated field of
    any model containing it, so a password reaches a log the first time something else is wrong.

    **Bind it with `to_instance`, never with `to`.** Its constructor takes a `str`, so the
    container would read that as a dependency on `str` and fail at resolve time asking for
    something nobody can register. There is no `register_static_value` helper to get this wrong
    with, because the correct line is already the clearest one in a wiring file::

        builder.register(DatabasePassword).to_instance(StaticValue("hunter2"))
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Hold `value`.

        Args:
            value: The value. Must not be blank — a component constructed with an empty key
                fails later as an authentication error from whatever it was talking to, which
                is a long way from the line that made the mistake. An application whose value
                is legitimately absent binds something else, rather than passing an empty one.

        Raises:
            ValueError: If `value` is empty or only whitespace.
        """
        if not value.strip():
            raise ValueError("A configuration value must not be empty.")
        self._value = value

    async def value(self) -> str:
        """Return the value. Awaits nothing."""
        return self._value

    def __repr__(self) -> str:
        """Name the class and disclose nothing."""
        return f"{type(self).__name__}(...)"
