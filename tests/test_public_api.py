"""Guards dexter's public import surface.

Every name a module re-exports is imported here, so a mis-edited ``__init__.py`` fails
the suite rather than a consumer's build.
"""

import subprocess
import sys

import dexter
from dexter.commons import DexterError, describe_type
from dexter.cqrs import (
    BusClosedError,
    Command,
    CommandBus,
    CommandHandler,
    CommandRegistry,
    CqrsError,
    CqrsGroupError,
    CqrsNotWiredError,
    CqrsRegistrationError,
    CqrsStateError,
    Dispatch,
    DispatchError,
    DispatchFailedError,
    DuplicateHandlerError,
    DuplicateMiddlewareError,
    Envelope,
    Event,
    EventBus,
    EventDispatch,
    EventHandler,
    EventHandlingError,
    EventRegistry,
    HandlerResultMismatchError,
    InProcessCommandBus,
    InProcessEventBus,
    InProcessQueryBus,
    InvalidHandlerError,
    Message,
    MessageBus,
    MessageId,
    MiddlewarePipeline,
    Query,
    QueryBus,
    QueryHandler,
    QueryRegistry,
    UnhandledCommandError,
    UnhandledMessageError,
    UnhandledQueryError,
    UnparameterizedMessageError,
    new_message_id,
    register_command_handler,
    register_event_handler,
    register_middleware,
    register_query_handler,
    use_cqrs,
)
from dexter.cqrs import (
    Middleware as CqrsMiddleware,
)
from dexter.cqrs import (
    Next as CqrsNext,
)
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


class TestCommonsSurface:
    def test_the_shared_type_renderer_is_exported(self) -> None:
        assert describe_type(DexterError) == "dexter.commons.errors.DexterError"


class TestCqrsSurface:
    def test_the_message_and_handler_contracts_are_exported(self) -> None:
        assert {Command, Query, Event}
        assert {CommandHandler, QueryHandler, EventHandler}
        assert {Envelope, Dispatch, EventDispatch, MessageBus}
        assert {CqrsMiddleware, CqrsNext, Message, MessageId, new_message_id}

    def test_the_buses_and_their_implementations_are_exported(self) -> None:
        assert issubclass(InProcessCommandBus, CommandBus)
        assert issubclass(InProcessQueryBus, QueryBus)
        assert issubclass(InProcessEventBus, EventBus)

    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {
            use_cqrs,
            register_command_handler,
            register_query_handler,
            register_event_handler,
            register_middleware,
        }

    def test_the_registries_and_pipeline_are_exported(self) -> None:
        assert {CommandRegistry, QueryRegistry, EventRegistry, MiddlewarePipeline}

    def test_every_error_is_exported(self) -> None:
        errors = {
            BusClosedError,
            CqrsError,
            CqrsGroupError,
            CqrsNotWiredError,
            CqrsRegistrationError,
            CqrsStateError,
            DispatchError,
            DispatchFailedError,
            DuplicateHandlerError,
            DuplicateMiddlewareError,
            EventHandlingError,
            HandlerResultMismatchError,
            InvalidHandlerError,
            UnhandledCommandError,
            UnhandledMessageError,
            UnhandledQueryError,
            UnparameterizedMessageError,
        }
        assert all(issubclass(error, CqrsError) for error in errors)

    def test_cqrs_errors_descend_from_dexter_error(self) -> None:
        assert issubclass(CqrsError, DexterError)

    def test_the_group_errors_are_exception_groups(self) -> None:
        # `except*` only works on a real ExceptionGroup, so this is load-bearing.
        assert issubclass(EventHandlingError, ExceptionGroup)
        assert issubclass(DispatchFailedError, ExceptionGroup)
