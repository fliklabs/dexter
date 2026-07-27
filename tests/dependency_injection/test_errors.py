"""The dependency injection error hierarchy and how failures are rendered.

Error quality is a feature here: a resolution failure that does not say how it was reached
sends the reader hunting through wiring code.
"""

import pytest

from dexter.commons import DexterError
from dexter.dependency_injection import (
    CircularDependencyError,
    ContainerBuilder,
    ContainerClosedError,
    ContainerStateError,
    DependencyInjectionError,
    DuplicateRegistrationError,
    IncompleteRegistrationError,
    InvalidRegistrationError,
    RegistrationError,
    ResolutionError,
    Scope,
    ScopeClosedError,
    UnregisteredDependencyError,
)

from .conftest import Db, Handler, Repository, SqlRepository


class TestHierarchy:
    def test_the_module_root_descends_from_the_dexter_root(self) -> None:
        assert issubclass(DependencyInjectionError, DexterError)

    @pytest.mark.parametrize(
        "error",
        [
            DuplicateRegistrationError,
            IncompleteRegistrationError,
            InvalidRegistrationError,
        ],
    )
    def test_wiring_failures_are_registration_errors(
        self, error: type[Exception]
    ) -> None:
        assert issubclass(error, RegistrationError)

    @pytest.mark.parametrize(
        "error", [UnregisteredDependencyError, CircularDependencyError]
    )
    def test_resolution_failures_are_resolution_errors(
        self, error: type[Exception]
    ) -> None:
        assert issubclass(error, ResolutionError)

    @pytest.mark.parametrize("error", [ContainerClosedError, ScopeClosedError])
    def test_lifecycle_failures_are_state_errors(self, error: type[Exception]) -> None:
        assert issubclass(error, ContainerStateError)

    def test_every_module_error_is_catchable_as_one_type(self) -> None:
        for error in (
            RegistrationError,
            ResolutionError,
            ContainerStateError,
        ):
            assert issubclass(error, DependencyInjectionError)


class TestChainRendering:
    async def test_renders_the_full_path_to_a_missing_dependency(
        self, builder: ContainerBuilder
    ) -> None:
        # Handler -> Repository -> Db, with Db deliberately unregistered.
        builder.register(Handler).to(Handler, scope=Scope.Transient)
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(Handler)

        rendered = str(raised.value)
        assert "Db is not registered in this container." in rendered
        assert "resolution chain:" in rendered
        assert "parameter 'repository'" in rendered
        assert "parameter 'db'" in rendered

    async def test_the_short_message_stays_a_one_liner_for_matching_and_logs(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Handler).to(Handler, scope=Scope.Transient)
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(Handler)

        # `args` carries only the summary; the chain is appended by `__str__`. Keys are
        # rendered module-qualified so two same-named classes stay distinguishable.
        assert raised.value.args == (
            "tests.dependency_injection.conftest.Db is not registered in this container.",
        )
        assert "\n" not in raised.value.args[0]

    async def test_exposes_the_offending_key_programmatically(
        self, builder: ContainerBuilder
    ) -> None:
        builder.register(Handler).to(Handler, scope=Scope.Transient)
        builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(Handler)

        assert raised.value.key is Db

    async def test_a_top_level_failure_renders_just_the_requested_key(
        self, builder: ContainerBuilder
    ) -> None:
        container = builder.build()

        with pytest.raises(UnregisteredDependencyError) as raised:
            await container.resolve(Db)

        rendered = str(raised.value)
        assert "Db is not registered" in rendered
        assert "parameter" not in rendered
