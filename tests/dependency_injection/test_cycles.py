"""Cycle detection, and the edges that must not be mistaken for cycles.

The cyclic classes are defined at module level on purpose. A forward reference is resolved
against module globals, so a class defined inside a test function cannot be referred to by
name before it exists — which is a property of Python's annotation scoping, not of dexter.
"""

import pytest

from dexter.dependency_injection import (
    CircularDependencyError,
    Container,
    ContainerBuilder,
    Scope,
)

from .conftest import Db


class Left:
    """One half of a two-node cycle."""

    def __init__(self, right: Right) -> None:
        self.right = right


class Right:
    """The other half, closing the cycle back onto `Left`."""

    def __init__(self, left: Left) -> None:
        self.left = left


class SelfDependent:
    """Depends directly on itself."""

    def __init__(self, other: SelfDependent) -> None:
        self.other = other


def register_cycle(builder: ContainerBuilder) -> Container:
    """Wire the two-node cycle and build the container."""
    builder.register(Left).to(Left, scope=Scope.TRANSIENT)
    builder.register(Right).to(Right, scope=Scope.TRANSIENT)
    return builder.build()


class TestEagerCycles:
    async def test_rejects_a_two_node_cycle(self, builder: ContainerBuilder) -> None:
        container = register_cycle(builder)
        with pytest.raises(CircularDependencyError, match="depends on itself"):
            await container.resolve(Left)

    async def test_names_the_key_that_closes_the_cycle(
        self, builder: ContainerBuilder
    ) -> None:
        container = register_cycle(builder)
        with pytest.raises(CircularDependencyError) as raised:
            await container.resolve(Left)

        assert raised.value.key is Left

    async def test_the_error_renders_the_path_that_produced_the_cycle(
        self, builder: ContainerBuilder
    ) -> None:
        container = register_cycle(builder)
        with pytest.raises(CircularDependencyError) as raised:
            await container.resolve(Left)

        rendered = str(raised.value)
        assert "resolution chain:" in rendered
        assert "parameter 'right'" in rendered
        assert "parameter 'left'" in rendered

    async def test_rejects_a_class_that_depends_on_itself(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(SelfDependent).to(SelfDependent, scope=Scope.TRANSIENT)
        container = builder.build()

        with pytest.raises(CircularDependencyError):
            await container.resolve(SelfDependent)

    async def test_a_cycle_is_detected_for_singletons_too(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Left).to(Left, scope=Scope.SINGLETON)
        builder.register(Right).to(Right, scope=Scope.SINGLETON)
        container = builder.build()

        with pytest.raises(CircularDependencyError):
            await container.resolve(Left)


class TestNonCycles:
    async def test_container_self_injection_is_not_a_cycle(
        self, builder: ContainerBuilder
    ) -> None:
        # A `Container` parameter is satisfied from the resolver itself, so it is not an edge
        # in the graph at all and cannot close a cycle.
        class UsesLocator:
            def __init__(self, container: Container) -> None:
                self.container = container

        builder.register(UsesLocator).to(UsesLocator, scope=Scope.TRANSIENT)
        container = builder.build()

        assert isinstance(await container.resolve(UsesLocator), UsesLocator)

    async def test_a_diamond_is_not_a_cycle(self, builder: ContainerBuilder) -> None:
        # Two dependents sharing one dependency revisits a key without repeating it on the
        # same path, which must be allowed.
        class LeftArm:
            def __init__(self, db: Db) -> None:
                self.db = db

        class RightArm:
            def __init__(self, db: Db) -> None:
                self.db = db

        class Top:
            def __init__(self, left: LeftArm, right: RightArm) -> None:
                self.left = left
                self.right = right

        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        for cls in (LeftArm, RightArm, Top):
            builder.register(cls).to(cls, scope=Scope.TRANSIENT)
        container = builder.build()

        top = await container.resolve(Top)
        assert top.left.db is top.right.db

    async def test_the_same_key_can_be_resolved_twice_in_sequence(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.TRANSIENT)
        container = builder.build()

        await container.resolve(Db)
        assert isinstance(await container.resolve(Db), Db)
