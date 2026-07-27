"""Registration guards: duplicates, incomplete bindings, and invalid providers."""

from typing import Any

import pytest

from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
    IncompleteRegistrationError,
    InvalidRegistrationError,
    Scope,
)

from .conftest import Db, Greeter, Hello, Repository, SqlRepository


class TestDuplicates:
    def test_raises_when_a_key_is_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        with pytest.raises(DuplicateRegistrationError, match="already registered"):
            builder.register(Db)

    def test_names_the_duplicated_key(self, builder: ContainerBuilder) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        with pytest.raises(DuplicateRegistrationError, match="Db"):
            builder.register(Db)


class TestIncompleteBindings:
    def test_build_raises_when_a_binding_was_never_completed(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db)  # no .to(...)
        with pytest.raises(IncompleteRegistrationError, match="never completed"):
            builder.build()

    def test_names_the_incomplete_key(self, builder: ContainerBuilder) -> None:
        builder.register(Db)
        with pytest.raises(IncompleteRegistrationError, match="Db"):
            builder.build()

    def test_build_succeeds_once_the_binding_is_completed(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Db).to(Db, scope=Scope.SINGLETON)
        assert builder.build().is_registered(Db)


class TestInvalidKeys:
    def test_rejects_a_key_that_is_not_a_class(self, builder: ContainerBuilder) -> None:
        not_a_class: Any = "Db"
        with pytest.raises(InvalidRegistrationError, match="must be a class"):
            builder.register(not_a_class)


class TestInvalidProviders:
    def test_rejects_a_provider_that_is_not_callable(
        self, builder: ContainerBuilder
    ) -> None:
        not_callable: Any = 42
        with pytest.raises(InvalidRegistrationError, match="not callable"):
            builder.register(Db).to(not_callable, scope=Scope.SINGLETON)

    def test_rejects_a_protocol_as_a_provider(self, builder: ContainerBuilder) -> None:
        protocol_provider: Any = Greeter
        with pytest.raises(InvalidRegistrationError, match="Protocol"):
            builder.register(Greeter).to(protocol_provider, scope=Scope.SINGLETON)

    def test_accepts_a_protocol_as_a_key(self, builder: ContainerBuilder) -> None:
        builder.register(Greeter).to(Hello, scope=Scope.SINGLETON)
        assert builder.build().is_registered(Greeter)


class TestEagerPlanning:
    def test_an_unannotated_parameter_fails_at_registration_not_resolution(
        self, builder: ContainerBuilder
    ) -> None:
        class Unannotated:
            def __init__(self, dependency) -> None:  # type: ignore[no-untyped-def]
                self.dependency = dependency

        provider: Any = Unannotated
        with pytest.raises(Exception, match="no annotation"):
            builder.register(Unannotated).to(provider, scope=Scope.TRANSIENT)

    def test_var_args_fails_at_registration(self, builder: ContainerBuilder) -> None:
        class Varargs:
            def __init__(self, *args: int) -> None:
                self.args = args

        with pytest.raises(Exception, match=r"\*args"):
            builder.register(Varargs).to(Varargs, scope=Scope.TRANSIENT)

    def test_positional_only_parameter_fails_at_registration(
        self, builder: ContainerBuilder
    ) -> None:
        class PositionalOnly:
            def __init__(self, db: Db, /) -> None:
                self.db = db

        with pytest.raises(Exception, match="positional-only"):
            builder.register(PositionalOnly).to(PositionalOnly, scope=Scope.TRANSIENT)

    def test_unresolvable_annotation_fails_at_registration(
        self, builder: ContainerBuilder
    ) -> None:
        class NeedsMissing:
            # The dangling name is the point: it must surface as a dexter error naming the
            # parameter, not as a NameError escaping from introspection.
            def __init__(self, dependency: DoesNotExist) -> None:  # type: ignore[name-defined] # noqa: F821
                self.dependency = dependency

        with pytest.raises(Exception, match="does not exist at runtime"):
            builder.register(NeedsMissing).to(NeedsMissing, scope=Scope.TRANSIENT)


class TestResolveInstance:
    def test_returns_the_registered_instance_before_build(
        self, builder: ContainerBuilder
    ) -> None:
        # The registry-mutation pattern: register a registry, then populate it while wiring.
        registry = Db()
        builder.register(Db).to_instance(registry)

        assert builder.resolve_instance(Db) is registry

    def test_raises_for_a_key_not_registered_as_an_instance(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Repository).to(SqlRepository, scope=Scope.TRANSIENT)
        with pytest.raises(
            InvalidRegistrationError, match="not registered as an instance"
        ):
            builder.resolve_instance(Repository)
