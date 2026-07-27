"""Regression tests for the five scope-correctness defects.

Each class here failed against the implementation that preceded it. They are grouped by defect
rather than by feature so the intent stays legible: this file exists to stop specific wrong
behaviours coming back.

Identity is asserted with `is` on retained objects, and the two defects that manifest as an
*extra* instance are asserted by counting constructions — comparing identities alone can miss a
duplicate.
"""

import pytest

from dexter.dependency_injection import (
    CaptiveDependencyError,
    Container,
    ContainerBuilder,
    ContainerClosedError,
    Scope,
    ScopeClosedError,
    ScopeRequiredError,
    UnregisteredDependencyError,
)


class Session:
    """A per-scope dependency that counts how many times it was built."""

    built = 0

    def __init__(self) -> None:
        Session.built += 1


class Pool:
    """A process-wide dependency that counts how many times it was built."""

    built = 0

    def __init__(self) -> None:
        Pool.built += 1


@pytest.fixture(autouse=True)
def _reset_counts() -> None:
    Session.built = 0
    Pool.built = 0


class AppService:
    """A singleton that wrongly wants a per-scope dependency."""

    def __init__(self, session: Session) -> None:
        self.session = session


class Middleman:
    """A transient standing between a singleton and a scoped dependency."""

    def __init__(self, session: Session) -> None:
        self.session = session


class IndirectService:
    """A singleton reaching a scoped dependency through a transient."""

    def __init__(self, middleman: Middleman) -> None:
        self.middleman = middleman


class LazyService:
    """A singleton that takes the container instead of holding a scoped dependency."""

    def __init__(self, container: Container) -> None:
        self.container = container


class TestCaptiveDependencyIsRejected:
    """Defect B: a singleton must not capture a scoped instance."""

    def test_direct_singleton_to_scoped_edge_is_rejected_at_build(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(AppService).to(AppService, scope=Scope.SINGLETON)

        with pytest.raises(CaptiveDependencyError, match=r"Scope\.SCOPED"):
            builder.build()

    def test_the_error_names_both_ends_of_the_edge(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(AppService).to(AppService, scope=Scope.SINGLETON)

        with pytest.raises(CaptiveDependencyError) as raised:
            builder.build()

        rendered = str(raised.value)
        assert "AppService" in rendered
        assert "Session" in rendered

    def test_a_transient_intermediary_does_not_hide_it(self) -> None:
        # Singleton -> Transient -> Scoped is still captive: the whole subgraph is built once,
        # on the root, so the scoped instance is captured just as permanently.
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(Middleman).to(Middleman, scope=Scope.TRANSIENT)
        builder.register(IndirectService).to(IndirectService, scope=Scope.SINGLETON)

        with pytest.raises(CaptiveDependencyError):
            builder.build()

    def test_nothing_is_constructed_before_the_error(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(AppService).to(AppService, scope=Scope.SINGLETON)

        with pytest.raises(CaptiveDependencyError):
            builder.build()

        # The point of validating at build time: it fails before anything is instantiated.
        assert Session.built == 0

    def test_a_container_parameter_breaks_the_chain(self) -> None:
        # Taking the container defers resolution to whatever scope is asking, so the singleton
        # never holds a scoped instance and the binding is legitimate.
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(LazyService).to(LazyService, scope=Scope.SINGLETON)

        assert builder.build().is_registered(LazyService)

    def test_a_scoped_consumer_of_a_scoped_dependency_is_allowed(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(AppService).to(AppService, scope=Scope.SCOPED)

        assert builder.build().is_registered(AppService)

    def test_a_transient_consumer_of_a_scoped_dependency_is_allowed(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        builder.register(AppService).to(AppService, scope=Scope.TRANSIENT)

        assert builder.build().is_registered(AppService)

    def test_a_singleton_depending_on_a_singleton_is_allowed(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SINGLETON)
        builder.register(AppService).to(AppService, scope=Scope.SINGLETON)

        assert builder.build().is_registered(AppService)


class TestScopedCannotBeResolvedFromTheRoot:
    """Defect C: the root is not a scope, so it has no scoped instance to give."""

    async def test_resolving_a_scoped_key_from_the_root_raises(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        container = builder.build()

        with pytest.raises(ScopeRequiredError, match="only be resolved inside a scope"):
            await container.resolve(Session)

    async def test_the_error_suggests_opening_a_scope(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        container = builder.build()

        with pytest.raises(ScopeRequiredError) as raised:
            await container.resolve(Session)

        assert "container.scope()" in str(raised.value)

    async def test_nothing_is_constructed_when_it_is_refused(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        container = builder.build()

        with pytest.raises(ScopeRequiredError):
            await container.resolve(Session)
        assert Session.built == 0

    async def test_the_same_key_resolves_happily_inside_a_scope(self) -> None:
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as scope:
            assert isinstance(await scope.resolve(Session), Session)


class TestCloseIsTerminal:
    """Defect A: a container is unusable once it, or any ancestor, is closed."""

    async def test_a_child_refuses_after_its_parent_closes(self) -> None:
        container = ContainerBuilder().build()
        outer = container.scope()
        inner = outer.scope()
        await outer.aclose()

        with pytest.raises(ScopeClosedError):
            await inner.resolve(Container)

    async def test_a_child_refuses_after_the_root_closes(self) -> None:
        container = ContainerBuilder().build()
        child = container.scope()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            await child.resolve(Container)

    async def test_a_grandchild_refuses_when_the_middle_scope_closed(self) -> None:
        container = ContainerBuilder().build()
        middle = container.scope()
        grandchild = middle.scope()
        await middle.aclose()

        with pytest.raises(ScopeClosedError):
            await grandchild.resolve(Container)

    async def test_a_scope_cannot_be_created_beneath_a_closed_ancestor(self) -> None:
        container = ContainerBuilder().build()
        outer = container.scope()
        inner = outer.scope()
        await outer.aclose()

        with pytest.raises(ScopeClosedError):
            inner.scope()

    async def test_a_closed_root_is_never_revived(self) -> None:
        # The original defect: a child resolving a singleton reached the closed root, built a
        # second instance and re-cached it there. Counting constructions is what catches that;
        # comparing identities alone would not.
        builder = ContainerBuilder()
        builder.register(Pool).to(Pool, scope=Scope.SINGLETON)
        container = builder.build()

        await container.resolve(Pool)
        assert Pool.built == 1

        child = container.scope()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            await child.resolve(Pool)
        assert Pool.built == 1

    async def test_try_resolve_also_refuses_beneath_a_closed_ancestor(self) -> None:
        container = ContainerBuilder().build()
        child = container.scope()
        await container.aclose()

        with pytest.raises(ContainerClosedError):
            await child.try_resolve(Pool)

    async def test_closing_an_inner_scope_leaves_its_parent_usable(self) -> None:
        # The fix must not over-reach: closing downwards must not disable upwards.
        builder = ContainerBuilder()
        builder.register(Session).to(Session, scope=Scope.SCOPED)
        container = builder.build()

        async with container.scope() as outer:
            inner = outer.scope()
            await inner.aclose()
            assert isinstance(await outer.resolve(Session), Session)


class TestContainerKeyIsConsistent:
    """Defect D: `resolve` and `try_resolve` must agree about the container itself."""

    async def test_resolve_returns_the_container(self) -> None:
        container = ContainerBuilder().build()
        assert await container.resolve(Container) is container

    async def test_try_resolve_also_returns_the_container(self) -> None:
        container = ContainerBuilder().build()
        assert await container.try_resolve(Container) is container

    async def test_both_agree_inside_a_scope(self) -> None:
        container = ContainerBuilder().build()
        async with container.scope() as scope:
            assert await scope.resolve(Container) is scope
            assert await scope.try_resolve(Container) is scope


class Missing:
    """Never registered anywhere."""


class NeedsMissing:
    """Registered, but its own dependency is not."""

    def __init__(self, missing: Missing) -> None:
        self.missing = missing


class HasDefault:
    """Declares a default, so a *directly* missing dependency is tolerable."""

    def __init__(self, inner: NeedsMissing | None = None) -> None:
        self.inner = inner


class TestDefaultsDoNotHideDeepFailures:
    """Defect E: a default covers its own key, not the whole subtree beneath it."""

    async def test_a_missing_transitive_dependency_still_raises(self) -> None:
        builder = ContainerBuilder()
        builder.register(NeedsMissing).to(NeedsMissing, scope=Scope.TRANSIENT)
        builder.register(HasDefault).to(HasDefault, scope=Scope.TRANSIENT)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(HasDefault)

        assert raised.value.key is Missing

    async def test_the_error_names_the_path_to_the_real_problem(self) -> None:
        builder = ContainerBuilder()
        builder.register(NeedsMissing).to(NeedsMissing, scope=Scope.TRANSIENT)
        builder.register(HasDefault).to(HasDefault, scope=Scope.TRANSIENT)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(HasDefault)

        rendered = str(raised.value)
        assert "parameter 'inner'" in rendered
        assert "parameter 'missing'" in rendered

    async def test_a_default_still_covers_its_own_missing_key(self) -> None:
        # The narrowing must not break the feature: when the annotated key itself is
        # unregistered, the default is still used.
        builder = ContainerBuilder()
        builder.register(HasDefault).to(HasDefault, scope=Scope.TRANSIENT)
        container = builder.build()

        assert (await container.resolve(HasDefault)).inner is None
