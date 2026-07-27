"""Constructor introspection edge cases.

Each case here is one where the obvious implementation is wrong, so these are the tests that
pin the planner's behaviour rather than its implementation.
"""

import functools
from collections.abc import Generator
from typing import Any, NamedTuple

import pytest

from dexter.dependency_injection import (
    ContainerBuilder,
    InvalidRegistrationError,
    ResolutionDepthExceededError,
    Scope,
)
from dexter.dependency_injection.models import ResolutionChain

from .conftest import Db, Greeter


class HasConstructor:
    """Declares its own constructor with a real dependency."""

    def __init__(self, db: Db) -> None:
        self.db = db


class InheritsConstructor(HasConstructor):
    """Declares nothing, but inherits a constructor with a real dependency.

    `"__init__" in vars(cls)` is `False` here, which is why that check cannot be used to
    decide whether a class has dependencies.
    """


class NoConstructorAnywhere:
    """Has only `object.__init__`, so it has no dependencies."""


class Point(NamedTuple):
    """Constructs through `__new__` rather than `__init__`."""

    db: Db


class TestInheritedConstructors:
    async def test_injects_dependencies_declared_on_a_base_class(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(InheritsConstructor).to(
            InheritsConstructor, scope=Scope.Transient
        )
        container = builder.build()

        resolved = await container.resolve(InheritsConstructor)
        assert isinstance(resolved.db, Db)

    async def test_a_class_with_no_constructor_resolves_with_no_dependencies(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(NoConstructorAnywhere).to(
            NoConstructorAnywhere, scope=Scope.Transient
        )
        container = builder.build()

        assert isinstance(
            await container.resolve(NoConstructorAnywhere), NoConstructorAnywhere
        )


class TestNewBasedConstruction:
    async def test_injects_into_a_namedtuple_which_constructs_via_new(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(Point).to(Point, scope=Scope.Transient)
        container = builder.build()

        point = await container.resolve(Point)
        assert isinstance(point.db, Db)


class TestPartialProviders:
    async def test_injects_the_parameters_a_partial_leaves_unbound(
        self, builder: ContainerBuilder
    ) -> None:
        def make(label: str, db: Db) -> HasConstructor:
            instance = HasConstructor(db)
            instance.db.name = label
            return instance

        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(HasConstructor).to(
            functools.partial(make, "partial"), scope=Scope.Transient
        )
        container = builder.build()

        assert (await container.resolve(HasConstructor)).db.name == "partial"


class TestRejectedConstructors:
    def test_rejects_keyword_var_args(self, builder: ContainerBuilder) -> None:
        class TakesKwargs:
            def __init__(self, **kwargs: int) -> None:
                self.kwargs = kwargs

        with pytest.raises(Exception, match=r"\*args/\*\*kwargs"):
            builder.register(TakesKwargs).to(TakesKwargs, scope=Scope.Transient)

    def test_rejects_a_protocol_as_the_thing_being_constructed(
        self, builder: ContainerBuilder
    ) -> None:
        provider: Any = Greeter
        with pytest.raises(Exception, match="Protocol"):
            builder.register(Greeter).to(provider, scope=Scope.Transient)


class TestPrimitiveParameters:
    async def test_an_unregistered_primitive_with_a_default_uses_the_default(
        self, builder: ContainerBuilder
    ) -> None:
        class TakesPrimitive:
            def __init__(self, name: str = "fallback") -> None:
                self.name = name

        builder.register(TakesPrimitive).to(TakesPrimitive, scope=Scope.Transient)
        container = builder.build()

        assert (await container.resolve(TakesPrimitive)).name == "fallback"

    async def test_an_unregistered_primitive_without_a_default_raises(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsPrimitive:
            def __init__(self, name: str) -> None:
                self.name = name

        builder.register(NeedsPrimitive).to(NeedsPrimitive, scope=Scope.Transient)
        container = builder.build()

        # `str` is not registered, and nothing should be invented for it.
        with pytest.raises(Exception, match="not registered"):
            await container.resolve(NeedsPrimitive)


class TestSyncProviderReturningAwaitable:
    async def test_raises_rather_than_silently_awaiting(
        self, builder: ContainerBuilder
    ) -> None:
        async def inner() -> Db:
            return Db()

        def looks_sync() -> Any:
            # Undetectable as async without calling it, so it is classified as sync.
            return inner()

        builder.register(Db).to(looks_sync, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(Exception, match="returned an awaitable"):
            await container.resolve(Db)


class TestResolutionDepthCeiling:
    """The depth backstop, for a runaway graph that eager cycle detection cannot see."""

    async def test_exceeding_the_ceiling_raises_a_dexter_error(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real ceiling is 200, far deeper than any sensible graph, so it is lowered here
        # rather than generating hundreds of classes. The branch under test is the same one.
        monkeypatch.setattr(ResolutionChain, "MAX_DEPTH", 2)

        class Third:
            def __init__(self) -> None: ...

        class Second:
            def __init__(self, third: Third) -> None:
                self.third = third

        class First:
            def __init__(self, second: Second) -> None:
                self.second = second

        for cls in (Third, Second, First):
            builder.register(cls).to(cls, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(ResolutionDepthExceededError, match="probably cyclic"):
            await container.resolve(First)

    async def test_a_graph_within_the_ceiling_still_resolves(
        self, builder: ContainerBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ResolutionChain, "MAX_DEPTH", 10)
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()

        assert isinstance(await container.resolve(Db), Db)


class TestAwaitableWithoutClose:
    """A sync provider returning an awaitable that is not a coroutine."""

    async def test_raises_without_trying_to_close_it(
        self, builder: ContainerBuilder
    ) -> None:
        # Coroutines have `.close()`, which the normal path calls to avoid a "never awaited"
        # warning. A bare awaitable does not, so the guard must cope with its absence.
        class BareAwaitable:
            def __await__(self) -> Generator[Any, Any, Db]:
                yield
                return Db()

        assert not hasattr(BareAwaitable(), "close")

        def looks_sync() -> Any:
            return BareAwaitable()

        builder.register(Db).to(looks_sync, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(InvalidRegistrationError, match="returned an awaitable"):
            await container.resolve(Db)
