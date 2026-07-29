"""Guards dexter's public import surface.

Every name a module re-exports is imported here, so a mis-edited ``__init__.py`` fails
the suite rather than a consumer's build.
"""

import subprocess
import sys

import dexter
from dexter.api import (
    ApiError,
    ApiHandler,
    ApiNotWiredError,
    ApiPipeline,
    ApiRegistrationError,
    ApiRequestError,
    ApiStateError,
    Cookie,
    DuplicateApiMiddlewareError,
    DuplicateExposureError,
    DuplicateRouteError,
    ErrorMap,
    ErrorMapping,
    ErrorResponse,
    Exposure,
    ExposureRecord,
    ExposureRegistry,
    Headers,
    HttpExposure,
    InvalidApiHandlerError,
    InvalidErrorMappingError,
    InvalidExposureError,
    Invocation,
    NoRequestContextError,
    PayloadSource,
    QueryValues,
    RequestContext,
    ResponseCommittedError,
    bind_request,
    current_request,
    default_payload,
    describe_source,
    path_parameters,
    register_api_middleware,
    register_error,
    register_handler,
    use_api,
)
from dexter.api import ApiMiddleware as ApiMiddlewareContract
from dexter.api import ApiNext as ApiNextContract
from dexter.api.http import create_app
from dexter.cli import (
    ACCENT,
    Capture,
    CliConsole,
    CliError,
    CliNotWiredError,
    CliRegistrationError,
    CommandTree,
    DuplicateCommandError,
    Field,
    FieldKind,
    InteractiveUnavailableError,
    InvalidCommandError,
    Outcome,
    describe_command,
    describe_kind,
    help_text,
    inject,
    invoke,
    read_fields,
    register_command,
    run,
    shell_command,
    use_cli,
)
from dexter.commons import DexterError, DexterGroupError, describe_type
from dexter.cqrs import (
    BusClosedError,
    BusGroup,
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
    DisposalError,
    Dispose,
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

    def test_disposal_is_exported(self) -> None:
        assert issubclass(DisposalError, DependencyInjectionError)
        assert issubclass(DisposalError, ExceptionGroup)
        assert Dispose is not None

    def test_the_scope_members_are_the_conventional_three(self) -> None:
        assert [scope.value for scope in Scope] == ["TRANSIENT", "SINGLETON", "SCOPED"]

    def test_scope_values_match_their_member_names(self) -> None:
        # The repo-wide enum convention; see AGENTS.md.
        assert all(scope.value == scope.name for scope in Scope)


class TestCommonsSurface:
    def test_the_shared_type_renderer_is_exported(self) -> None:
        assert describe_type(DexterError) == "dexter.commons.errors.DexterError"

    def test_the_shared_group_error_is_exported(self) -> None:
        assert issubclass(DexterGroupError, DexterError)
        assert issubclass(DexterGroupError, ExceptionGroup)


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
        # Note `dexter.cli` deliberately calls its own equivalent `CommandTree`, so these two
        # modules can be imported together without one shadowing the other.
        assert {CommandRegistry, QueryRegistry, EventRegistry, MiddlewarePipeline}

    def test_the_bus_group_is_exported(self) -> None:
        # Applications binding their own bus implementation need it in the constructor.
        assert hasattr(BusGroup, "settle")

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


class TestCliSurface:
    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {use_cli, register_command, inject, run, invoke}

    def test_the_registry_and_console_are_exported(self) -> None:
        assert {CommandTree, CliConsole, Capture, Outcome}

    def test_the_form_types_are_exported(self) -> None:
        # An application rendering its own menu needs these to describe a command.
        assert {
            Field,
            FieldKind,
            read_fields,
            describe_command,
            shell_command,
            help_text,
        }

    def test_every_error_is_exported(self) -> None:
        errors = {
            CliError,
            CliNotWiredError,
            CliRegistrationError,
            DuplicateCommandError,
            InteractiveUnavailableError,
            InvalidCommandError,
        }
        assert all(issubclass(error, CliError) for error in errors)

    def test_cli_errors_descend_from_dexter_error(self) -> None:
        assert issubclass(CliError, DexterError)

    def test_the_colour_vocabulary_is_exported(self) -> None:
        assert isinstance(ACCENT, str)

    def test_the_field_kinds_follow_the_enum_convention(self) -> None:
        assert all(kind.value == kind.name for kind in FieldKind)
        assert describe_kind(FieldKind.FLAG) == "FieldKind.FLAG"

    def test_importing_the_cli_does_not_import_curses(self) -> None:
        """`dexter.cli` must stay importable where curses is not available."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.cli; print('curses' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.cli pulled in curses, which is not available everywhere"
        )


class TestApiSurface:
    def test_the_handler_and_middleware_contracts_are_exported(self) -> None:
        assert {ApiHandler, ApiMiddlewareContract, ApiNextContract, Invocation}

    def test_the_request_context_and_its_value_types_are_exported(self) -> None:
        assert {RequestContext, Headers, QueryValues, Cookie}
        assert {bind_request, current_request}

    def test_the_exposure_types_are_exported(self) -> None:
        # The protocol seam: a second transport subclasses `Exposure` and asks the registry
        # for its own kind.
        assert issubclass(HttpExposure, Exposure)
        assert {PayloadSource, path_parameters, default_payload, describe_source}

    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {use_api, register_handler, register_api_middleware, register_error}

    def test_the_registries_and_pipeline_are_exported(self) -> None:
        # Named with an `Api` prefix where `dexter.cqrs` already owns the plain name, so an
        # application wiring both can import them into one file.
        assert {ExposureRegistry, ExposureRecord, ErrorMap, ErrorMapping, ApiPipeline}

    def test_the_error_body_is_exported(self) -> None:
        assert set(ErrorResponse.model_fields) == {"title", "status", "detail"}

    def test_every_error_is_exported(self) -> None:
        errors = {
            ApiError,
            ApiNotWiredError,
            ApiRegistrationError,
            ApiRequestError,
            ApiStateError,
            DuplicateApiMiddlewareError,
            DuplicateExposureError,
            DuplicateRouteError,
            InvalidApiHandlerError,
            InvalidErrorMappingError,
            InvalidExposureError,
            NoRequestContextError,
            ResponseCommittedError,
        }
        assert all(issubclass(error, ApiError) for error in errors)
        assert issubclass(ApiError, DexterError)

    def test_the_payload_sources_follow_the_enum_convention(self) -> None:
        assert all(source.value == source.name for source in PayloadSource)
        assert describe_source(PayloadSource.BODY) == "PayloadSource.BODY"

    def test_the_application_builder_lives_in_the_http_adapter(self) -> None:
        """`create_app` is deliberately not re-exported from `dexter.api`."""
        assert not hasattr(dexter.api, "create_app")
        assert create_app.__module__.startswith("dexter.api.http")

    def test_importing_the_api_does_not_import_a_web_framework(self) -> None:
        """The core is transport-agnostic; `dexter.api.http` is where the framework lives."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.api; "
                "print(any(m in sys.modules for m in ('fastapi', 'starlette')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.api pulled in a web framework; the seam has been broken"
        )
