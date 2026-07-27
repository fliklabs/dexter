"""Guards dexter's public import surface.

Every name a module re-exports is imported here, so a mis-edited ``__init__.py`` fails
the suite rather than a consumer's build.
"""

import subprocess
import sys

import dexter
from dexter.commons import DexterError
from dexter.dependency_injection import (
    Binder,
    CaptiveDependencyError,
    CircularDependencyError,
    Container,
    ContainerBuilder,
    ContainerClosedError,
    ContainerStateError,
    DependencyInjectionError,
    DuplicateRegistrationError,
    IncompleteRegistrationError,
    InvalidRegistrationError,
    PositionalOnlyParameterError,
    Registration,
    RegistrationError,
    ResolutionDepthExceededError,
    ResolutionError,
    Scope,
    ScopeClosedError,
    ScopeRequiredError,
    UnregisteredDependencyError,
    UnresolvableParameterError,
    UnresolvedAnnotationError,
)


class TestTopLevelPackage:
    def test_exposes_a_version_string(self) -> None:
        assert isinstance(dexter.__version__, str)
        assert dexter.__version__

    def test_importing_dexter_does_not_import_framework_modules(self) -> None:
        """``import dexter`` must stay cheap and pull in no framework modules."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter; "
                "print(sorted(m for m in sys.modules if m.startswith('dexter.')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "[]", (
            f"importing dexter dragged in submodules: {result.stdout.strip()}"
        )


class TestExceptionHierarchy:
    def test_every_module_error_descends_from_dexter_error(self) -> None:
        assert issubclass(DependencyInjectionError, DexterError)

    def test_dexter_error_is_an_exception(self) -> None:
        assert issubclass(DexterError, Exception)


class TestDependencyInjectionSurface:
    def test_the_wiring_and_resolution_types_are_exported(self) -> None:
        assert {Binder, Container, ContainerBuilder, Registration, Scope}

    def test_every_error_is_exported(self) -> None:
        # Consumers cannot handle what they cannot import, so the whole tree is public.
        errors = {
            CaptiveDependencyError,
            CircularDependencyError,
            ContainerClosedError,
            ContainerStateError,
            DependencyInjectionError,
            DuplicateRegistrationError,
            IncompleteRegistrationError,
            InvalidRegistrationError,
            PositionalOnlyParameterError,
            RegistrationError,
            ResolutionDepthExceededError,
            ResolutionError,
            ScopeClosedError,
            ScopeRequiredError,
            UnregisteredDependencyError,
            UnresolvableParameterError,
            UnresolvedAnnotationError,
        }
        assert all(issubclass(error, DependencyInjectionError) for error in errors)

    def test_the_scope_members_are_the_conventional_three(self) -> None:
        assert [scope.value for scope in Scope] == ["TRANSIENT", "SINGLETON", "SCOPED"]

    def test_scope_values_match_their_member_names(self) -> None:
        # The repo-wide enum convention; see AGENTS.md.
        assert all(scope.value == scope.name for scope in Scope)
