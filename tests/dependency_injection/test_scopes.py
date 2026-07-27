"""Scope semantics: Transient, Singleton and Scoped, including nesting.

Identity is asserted with `is` on objects held in locals. Never compare `id()` of an object that
may already have been collected — CPython reuses addresses, so a freshly-collected transient can
appear to be the same object as an unrelated later one.
"""

from dexter.dependency_injection import Container, ContainerBuilder, Scope

from .conftest import Db


class Leaf:
    """A dependency with nothing of its own."""


class ScopedHolder:
    """A scoped consumer of a scoped dependency, to check ownership through an edge."""

    def __init__(self, leaf: Leaf) -> None:
        self.leaf = leaf


class TransientHolder:
    """A transient consumer of a scoped dependency."""

    def __init__(self, leaf: Leaf) -> None:
        self.leaf = leaf


class TestTransient:
    async def test_returns_a_new_instance_for_every_resolution(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.TRANSIENT)
        container = builder.build()

        assert await container.resolve(Db) is not await container.resolve(Db)


class TestSingleton:
    async def test_returns_the_same_instance_for_every_resolution(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        container = builder.build()

        assert await container.resolve(Db) is await container.resolve(Db)

    async def test_is_shared_across_scopes(self, builder: ContainerBuilder) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        container = builder.build()

        async with container.scope() as first, container.scope() as second:
            assert await first.resolve(Db) is await second.resolve(Db)

    async def test_a_scope_sees_the_instance_the_root_already_built(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        container = builder.build()

        from_root = await container.resolve(Db)
        async with container.scope() as scope:
            assert await scope.resolve(Db) is from_root


class TestScoped:
    async def test_returns_the_same_instance_within_one_scope(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as scope:
            assert await scope.resolve(Db) is await scope.resolve(Db)

    async def test_returns_different_instances_in_different_scopes(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as first, container.scope() as second:
            assert await first.resolve(Db) is not await second.resolve(Db)

    async def test_a_nested_scope_gets_its_own_instance(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as outer:
            outer_db = await outer.resolve(Db)
            async with outer.scope() as inner:
                assert await inner.resolve(Db) is not outer_db


class TestNestingDepth:
    """Scoping is arbitrarily deep: each level is its own scope."""

    async def test_three_levels_each_get_their_own_scoped_instance(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as one, one.scope() as two, two.scope() as three:
            first = await one.resolve(Db)
            second = await two.resolve(Db)
            third = await three.resolve(Db)

        assert first is not second
        assert second is not third
        assert first is not third

    async def test_a_singleton_is_identical_at_every_depth(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        container = builder.build()

        from_root = await container.resolve(Db)
        async with container.scope() as one, one.scope() as two, two.scope() as three:
            assert await one.resolve(Db) is from_root
            assert await two.resolve(Db) is from_root
            assert await three.resolve(Db) is from_root

    async def test_a_transient_is_distinct_inside_a_nested_scope(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.TRANSIENT)
        container = builder.build()

        async with container.scope() as one, one.scope() as two:
            held = [await two.resolve(Db) for _ in range(3)]

        assert held[0] is not held[1]
        assert held[1] is not held[2]
        assert held[0] is not held[2]

    async def test_a_singleton_first_built_inside_a_scope_is_seen_by_the_root(
        self, builder: ContainerBuilder
    ) -> None:
        # The reverse of the existing root-first test: ownership is the root's either way.
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        container = builder.build()

        async with container.scope() as scope:
            from_scope = await scope.resolve(Db)

        assert await container.resolve(Db) is from_scope


class TestScopedOwnershipThroughDependencies:
    """A scoped dependency belongs to the scope that resolved its consumer."""

    async def test_a_transient_consumer_gets_its_own_scopes_dependency(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Leaf).to(Leaf, scope=Scope.SCOPED)
        builder.register(TransientHolder).to(TransientHolder, scope=Scope.TRANSIENT)
        container = builder.build()

        async with container.scope() as one, one.scope() as two:
            outer_leaf = await one.resolve(Leaf)
            inner_leaf = await two.resolve(Leaf)
            # Asserting the two scopes differ is what makes this test meaningful: without it,
            # an implementation that cached every scoped instance on the root would still pass,
            # because both sides of each comparison would be that one shared object.
            assert outer_leaf is not inner_leaf
            assert (await one.resolve(TransientHolder)).leaf is outer_leaf
            assert (await two.resolve(TransientHolder)).leaf is inner_leaf

    async def test_a_scoped_consumer_gets_its_own_scopes_dependency(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Leaf).to(Leaf, scope=Scope.SCOPED)
        builder.register(ScopedHolder).to(ScopedHolder, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as one, one.scope() as two:
            assert (await one.resolve(ScopedHolder)).leaf is await one.resolve(Leaf)
            assert (await two.resolve(ScopedHolder)).leaf is await two.resolve(Leaf)

    async def test_two_consumers_in_one_scope_share_one_scoped_dependency(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Leaf).to(Leaf, scope=Scope.SCOPED)
        builder.register(ScopedHolder).to(ScopedHolder, scope=Scope.SCOPED)
        builder.register(TransientHolder).to(TransientHolder, scope=Scope.TRANSIENT)
        container = builder.build()

        async with container.scope() as scope:
            scoped_consumer = await scope.resolve(ScopedHolder)
            transient_consumer = await scope.resolve(TransientHolder)
            assert scoped_consumer.leaf is transient_consumer.leaf


class TestScopeIsolation:
    """Sibling and cousin scopes never share a scoped instance."""

    async def test_cousin_scopes_do_not_share(self, builder: ContainerBuilder) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        # Later items in one `async with` may reference earlier ones, so the two generations
        # of scope open in a single statement.
        async with (
            container.scope() as left,
            container.scope() as right,
            left.scope() as left_child,
            right.scope() as right_child,
        ):
            assert await left_child.resolve(Db) is not await right_child.resolve(Db)

    async def test_a_parents_instance_survives_its_child_closing(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as parent:
            before = await parent.resolve(Db)
            async with parent.scope() as child:
                await child.resolve(Db)
            assert await parent.resolve(Db) is before


class TestInstanceBindingsIgnoreScoping:
    """`to_instance` bypasses the scope machinery entirely."""

    async def test_the_same_instance_at_every_depth(
        self, builder: ContainerBuilder
    ) -> None:
        sentinel = Db()
        builder.register(Db).to_instance(sentinel)
        container = builder.build()

        async with container.scope() as one, one.scope() as two:
            assert await container.resolve(Db) is sentinel
            assert await one.resolve(Db) is sentinel
            assert await two.resolve(Db) is sentinel

    async def test_an_instance_that_is_itself_none_is_returned_not_treated_as_absent(
        self, builder: ContainerBuilder
    ) -> None:
        # `has_instance` exists precisely so a bound `None` is distinguishable from unbound.
        builder.register(Leaf).to_instance(None)  # type: ignore[arg-type]
        container = builder.build()

        assert await container.resolve(Leaf) is None
        assert container.is_registered(Leaf)

    async def test_an_instance_is_injected_as_a_dependency(
        self, builder: ContainerBuilder
    ) -> None:
        sentinel = Leaf()
        builder.register(Leaf).to_instance(sentinel)
        builder.register(TransientHolder).to(TransientHolder, scope=Scope.TRANSIENT)
        container = builder.build()

        assert (await container.resolve(TransientHolder)).leaf is sentinel


class TestContainerKeyDepth:
    """`Container` self-injection resolves to the innermost container, at any depth."""

    async def test_resolve_container_returns_the_innermost_scope(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()
        async with container.scope() as one, one.scope() as two, two.scope() as three:
            assert await three.resolve(Container) is three
            assert await two.resolve(Container) is two
            assert await one.resolve(Container) is one
            assert await container.resolve(Container) is container
