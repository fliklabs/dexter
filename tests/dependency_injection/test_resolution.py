"""Resolution: constructor injection, self-injection, optionals, and async providers."""

import pytest

from dexter.dependency_injection import (
    Container,
    ContainerBuilder,
    Scope,
    UnregisteredDependencyError,
)

from .conftest import Db, Greeter, Handler, Hello, Repository, SqlRepository


class TestHappyPath:
    async def test_resolves_a_dependency_with_no_dependencies_of_its_own(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()
        assert (await container.resolve(Db)).name == "db"

    async def test_injects_a_whole_graph_by_keyword(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        builder.register(Handler).to(Handler, scope=Scope.Transient)
        container = builder.build()

        handler = await container.resolve(Handler)
        assert handler.repository.find() == "row from db"

    async def test_resolves_an_abstract_key_to_its_implementation(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        container = builder.build()

        assert isinstance(await container.resolve(Repository), SqlRepository)

    async def test_resolves_a_protocol_key(self, builder: ContainerBuilder) -> None:
        builder.register(Greeter).to(Hello, scope=Scope.Singleton)
        container = builder.build()

        assert (await container.resolve(Greeter)).greet() == "hello"

    async def test_returns_a_registered_instance_unchanged(
        self, builder: ContainerBuilder
    ) -> None:
        sentinel = Db()
        builder.register(Db).to_instance(sentinel)
        container = builder.build()

        assert await container.resolve(Db) is sentinel


class TestUnregistered:
    async def test_raises_rather_than_constructing_an_unregistered_class(self) -> None:
        container = ContainerBuilder().build()
        with pytest.raises(UnregisteredDependencyError):
            await container.resolve(Db)

    async def test_try_resolve_returns_none_for_an_unregistered_key(self) -> None:
        container = ContainerBuilder().build()
        assert await container.try_resolve(Db) is None

    async def test_try_resolve_still_raises_when_a_registered_binding_fails(
        self, builder: ContainerBuilder
    ) -> None:
        # Repository is bound but its Db dependency is not, so this is a wiring bug rather
        # than an absent optional feature.
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError):
            await container.try_resolve(Repository)


class TestSelfInjection:
    async def test_injects_the_resolving_container(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsContainer:
            def __init__(self, container: Container) -> None:
                self.container = container

        builder.register(NeedsContainer).to(NeedsContainer, scope=Scope.Transient)
        container = builder.build()

        assert (await container.resolve(NeedsContainer)).container is container

    async def test_injects_the_scope_not_the_root_when_resolved_in_a_scope(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsContainer:
            def __init__(self, container: Container) -> None:
                self.container = container

        builder.register(NeedsContainer).to(NeedsContainer, scope=Scope.Transient)
        container = builder.build()

        async with container.scope() as scope:
            resolved = await scope.resolve(NeedsContainer)
            assert resolved.container is scope
            assert resolved.container is not container


class TestOptionalDependencies:
    async def test_injects_none_when_an_optional_dependency_is_unregistered(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsOptional:
            def __init__(self, db: Db | None) -> None:
                self.db = db

        builder.register(NeedsOptional).to(NeedsOptional, scope=Scope.Transient)
        container = builder.build()

        assert (await container.resolve(NeedsOptional)).db is None

    async def test_injects_the_value_when_an_optional_dependency_is_registered(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsOptional:
            def __init__(self, db: Db | None) -> None:
                self.db = db

        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(NeedsOptional).to(NeedsOptional, scope=Scope.Transient)
        container = builder.build()

        assert isinstance((await container.resolve(NeedsOptional)).db, Db)


class TestConstructorDefaults:
    async def test_falls_back_to_the_default_when_a_dependency_is_unregistered(
        self, builder: ContainerBuilder
    ) -> None:
        fallback = Db()

        class NeedsDefault:
            def __init__(self, db: Db = fallback) -> None:
                self.db = db

        builder.register(NeedsDefault).to(NeedsDefault, scope=Scope.Transient)
        container = builder.build()

        assert (await container.resolve(NeedsDefault)).db is fallback

    async def test_prefers_a_registered_dependency_over_the_default(
        self, builder: ContainerBuilder
    ) -> None:
        fallback = Db()

        class NeedsDefault:
            def __init__(self, db: Db = fallback) -> None:
                self.db = db

        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(NeedsDefault).to(NeedsDefault, scope=Scope.Transient)
        container = builder.build()

        assert (await container.resolve(NeedsDefault)).db is not fallback


class TestAsyncProviders:
    async def test_awaits_an_async_factory(self, builder: ContainerBuilder) -> None:
        async def open_db() -> Db:
            db = Db()
            db.name = "opened"
            return db

        builder.register(Db).to(open_db, scope=Scope.Singleton)
        container = builder.build()

        assert (await container.resolve(Db)).name == "opened"

    async def test_injects_dependencies_into_an_async_factory(
        self, builder: ContainerBuilder
    ) -> None:
        async def open_repository(db: Db) -> Repository:
            return SqlRepository(db)

        builder.register(Db).to(Db, scope=Scope.Singleton)
        builder.register(Repository).to(open_repository, scope=Scope.Singleton)
        container = builder.build()

        assert (await container.resolve(Repository)).find() == "row from db"

    async def test_awaits_a_callable_object_with_an_async_call(
        self, builder: ContainerBuilder
    ) -> None:
        class OpenDb:
            async def __call__(self) -> Db:
                db = Db()
                db.name = "called"
                return db

        builder.register(Db).to(OpenDb(), scope=Scope.Singleton)
        container = builder.build()

        assert (await container.resolve(Db)).name == "called"


class TestTryResolveInScopes:
    """`try_resolve` must respect scoping exactly as `resolve` does."""

    async def test_returns_a_real_instance_for_a_bound_key(
        self, builder: ContainerBuilder
    ) -> None:
        # The success path: both existing try_resolve tests take the None or raising path.
        builder.register(Db).to(Db, scope=Scope.Singleton)
        container = builder.build()

        assert isinstance(await container.try_resolve(Db), Db)

    async def test_a_scoped_key_is_stable_within_one_scope(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            assert await scope.try_resolve(Db) is await scope.try_resolve(Db)

    async def test_a_scoped_key_differs_between_scopes(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as first, container.scope() as second:
            assert await first.try_resolve(Db) is not await second.try_resolve(Db)

    async def test_agrees_with_resolve_about_a_scoped_key(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.Scoped)
        container = builder.build()

        async with container.scope() as scope:
            assert await scope.try_resolve(Db) is await scope.resolve(Db)


class TestOptionalDependenciesPerScope:
    """An optional dependency is evaluated per resolving container, not once globally."""

    async def test_an_optional_scoped_dependency_is_its_own_scopes(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsOptionalScoped:
            def __init__(self, db: Db | None) -> None:
                self.db = db

        builder.register(Db).to(Db, scope=Scope.Scoped)
        builder.register(NeedsOptionalScoped).to(
            NeedsOptionalScoped, scope=Scope.Transient
        )
        container = builder.build()

        async with container.scope() as first, container.scope() as second:
            in_first = await first.resolve(NeedsOptionalScoped)
            in_second = await second.resolve(NeedsOptionalScoped)
            assert in_first.db is await first.resolve(Db)
            assert in_second.db is await second.resolve(Db)
            assert in_first.db is not in_second.db

    async def test_an_unregistered_optional_is_none_inside_a_scope_too(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsOptional:
            def __init__(self, db: Db | None) -> None:
                self.db = db

        builder.register(NeedsOptional).to(NeedsOptional, scope=Scope.Transient)
        container = builder.build()

        async with container.scope() as scope:
            assert (await scope.resolve(NeedsOptional)).db is None
