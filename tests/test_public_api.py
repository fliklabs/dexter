"""Guards dexter's public import surface.

Every name a module re-exports is imported here, so a mis-edited ``__init__.py`` fails
the suite rather than a consumer's build.
"""

import subprocess
import sys

import dexter
import dexter.aws
import dexter.tools
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
    InvalidField,
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
from dexter.application import (
    ApplicationError,
    ApplicationNotWiredError,
    DuplicateModuleError,
    InvalidModuleError,
    Module,
    ModuleRegistry,
    describe_module,
    register_module,
    use_application,
)
from dexter.aws import (
    AccessDeniedError,
    And,
    Attr,
    AttributeType,
    AwsConfig,
    AwsEndpoints,
    AwsError,
    AwsRequestError,
    AwsSession,
    AwsWiringError,
    BatchFailure,
    BatchIncompleteError,
    BatchResult,
    BatchSuccess,
    BeginsWith,
    Between,
    Comparison,
    ComparisonOperator,
    Condition,
    ConditionFailedError,
    Contains,
    CredentialsUnavailableError,
    DeleteFailure,
    DeleteReport,
    DeleteRequest,
    DynamoDbClient,
    EmailRejectedError,
    Exists,
    In,
    Item,
    ItemEncodingError,
    ItemKey,
    ItemPage,
    ItemStream,
    MessageAttribute,
    MessageTooLargeError,
    Not,
    NotExists,
    ObjectNotFoundError,
    ObjectPage,
    ObjectSummary,
    Or,
    OutboundMessage,
    ParameterNotFoundError,
    ParameterStoreClient,
    ParameterValue,
    PutRequest,
    ReceivedMessage,
    ResourceNotFoundError,
    RetryMode,
    S3Client,
    SecretNotFoundError,
    SecretsManagerClient,
    SecretValue,
    SesClient,
    SmsType,
    SnsClient,
    SqsClient,
    StaticValue,
    ThrottledError,
    TransactConditionCheck,
    TransactDelete,
    TransactGet,
    TransactionConflictError,
    TransactPut,
    TransactUpdate,
    TransactWrite,
    TtlCache,
    ValueSource,
    WriteRequest,
    describe_comparison_operator,
    describe_retry_mode,
    describe_sms_type,
    register_aws_config,
    register_parameter_value,
    register_secret_value,
    use_aws,
)
from dexter.aws import Key as DynamoKey
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
from dexter.iam import (
    DIGITS,
    HMAC_ALGORITHMS,
    Claim,
    Clock,
    DuplicateAuthenticationRuleError,
    ExpiredTokenError,
    IamError,
    IamNotWiredError,
    IamRegistrationError,
    InMemoryMagicCodeStore,
    InvalidTokenError,
    JwtCodec,
    MagicCode,
    MagicCodeError,
    MagicCodeExpiredError,
    MagicCodeMismatchError,
    MagicCodePolicy,
    MagicCodeService,
    MagicCodeStore,
    MagicCodeThrottledError,
    NoMagicCodeError,
    NotAuthenticatedError,
    Principal,
    SystemClock,
    TokenError,
    TokenKind,
    TokenPair,
    TokenPolicy,
    TokenService,
    TooManyAttemptsError,
    WrongTokenKindError,
    describe_token_kind,
    register_magic_code_policy,
    register_token_policy,
    use_iam,
    use_in_memory_magic_codes,
)
from dexter.iam.api import (
    HEADER,
    SCHEME,
    STATE_KEY,
    UNAUTHORIZED,
    Authentication,
    AuthenticationMiddleware,
    AuthenticationRegistry,
    AuthenticationRequirement,
    current_authentication,
    current_principal,
    describe_requirement,
    require_authentication,
    use_authentication,
)
from dexter.notification import (
    DeliveryError,
    Email,
    EmailBody,
    EmailBodyType,
    EmailNotifier,
    NotificationError,
    RecordingEmailNotifier,
    describe_body_type,
    use_recording_notification,
)
from dexter.notification.resend import (
    ENDPOINT,
    RESEND_FIELD,
    ResendConfig,
    ResendEmailNotifier,
    register_resend_config,
    use_resend_notification,
)
from dexter.notification.ses import SesEmailNotifier, use_ses_notification
from dexter.tools.pins import (
    Change,
    declared,
    locked,
    moved,
    raise_floors,
    raised,
    rewrite,
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


class TestToolsSurface:
    """The one part of dexter that is tooling rather than framework."""

    def test_the_pin_helpers_are_importable(self) -> None:
        assert {Change, locked, declared, raised, rewrite, moved, raise_floors}

    def test_the_package_does_not_import_its_own_runnable_submodule(self) -> None:
        """Otherwise `python -m dexter.tools.pins` executes `pins` twice.

        `runpy` warns on every invocation when it does, and that warning is an error for any
        consumer running pytest with `filterwarnings = ["error"]` — as this repository does.
        `json` and `json.tool` are the stdlib precedent for the shape.

        Checked in a subprocess because importing the submodule anywhere binds it onto the
        parent package, so in-process there is nothing left to observe.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.tools; print('dexter.tools.pins' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "dexter/tools/__init__.py imports .pins, which makes `-m` run it twice"
        )

    def test_running_it_as_a_module_is_warning_free(self) -> None:
        """The shipped command-line interface, run the way a consumer's script runs it."""
        result = subprocess.run(
            [sys.executable, "-W", "error", "-m", "dexter.tools.pins"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "usage" in result.stdout
        assert not result.stderr

    def test_it_is_not_wired_into_a_container(self) -> None:
        """Nothing here is a service, so there is no `use_tools` to call."""
        assert not [name for name in dir(dexter.tools) if name.startswith("use_")]

    def test_it_costs_a_consumer_no_dependency(self) -> None:
        """It ships in the wheel, so it may only ever import the standard library.

        This is the entire justification for a development-time tool living in a runtime
        package. The moment it needs a third party, it belongs outside `dexter/` instead.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.tools; "
                "print(sorted({m.split('.')[0] for m in sys.modules "
                "if not m.startswith(('_', 'dexter')) and '.' not in m}))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        for third_party in ("click", "rich", "pydantic", "fastapi"):
            assert third_party not in result.stdout, (
                f"dexter.tools imported {third_party}; it must stay standard library only"
            )


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
        # One shape for every failure, whichever layer raised it. `errors` is an RFC 9457
        # extension member, present only on a validation failure.
        assert set(ErrorResponse.model_fields) == {
            "title",
            "status",
            "detail",
            "errors",
        }
        assert set(InvalidField.model_fields) == {"location", "message", "kind"}

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


class TestApplicationSurface:
    def test_the_module_contract_is_exported(self) -> None:
        assert Module is not None
        assert describe_module(use_application) == "use_application"

    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {use_application, register_module}

    def test_the_registry_is_exported(self) -> None:
        # Named `ModuleRegistry`, not `Registry`: `dexter.cqrs` and `dexter.cli` already own
        # `CommandRegistry` and `CommandTree`, and an application imports all three.
        assert len(ModuleRegistry()) == 0

    def test_every_error_is_exported(self) -> None:
        errors = {
            ApplicationError,
            ApplicationNotWiredError,
            DuplicateModuleError,
            InvalidModuleError,
        }
        assert all(issubclass(error, ApplicationError) for error in errors)
        assert issubclass(ApplicationError, DexterError)


class TestIamSurface:
    def test_the_caller_and_token_types_are_exported(self) -> None:
        assert {Principal, Claim, TokenPair, TokenKind, MagicCode}

    def test_the_policies_are_exported(self) -> None:
        # Bound by the application with `register_*`, never passed to a `use_*`: a topology
        # switch is not a settings object.
        assert {TokenPolicy, MagicCodePolicy}

    def test_the_services_are_exported(self) -> None:
        assert {TokenService, MagicCodeService, JwtCodec}

    def test_the_seams_and_their_shipped_implementations_are_exported(self) -> None:
        assert {Clock, MagicCodeStore}
        assert {SystemClock, InMemoryMagicCodeStore}

    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {
            use_iam,
            use_in_memory_magic_codes,
            register_token_policy,
            register_magic_code_policy,
        }

    def test_the_algorithm_whitelist_names_only_the_symmetric_family(self) -> None:
        # `none` is the classic JWT forgery and must never be reachable from configuration.
        assert {"HS256", "HS384", "HS512"} == HMAC_ALGORITHMS
        assert DIGITS == "0123456789"

    def test_every_error_is_exported(self) -> None:
        errors = {
            IamError,
            IamNotWiredError,
            IamRegistrationError,
            DuplicateAuthenticationRuleError,
            TokenError,
            InvalidTokenError,
            ExpiredTokenError,
            WrongTokenKindError,
            NotAuthenticatedError,
            MagicCodeError,
            MagicCodeExpiredError,
            MagicCodeMismatchError,
            MagicCodeThrottledError,
            NoMagicCodeError,
            TooManyAttemptsError,
        }
        assert all(issubclass(error, IamError) for error in errors)
        assert issubclass(IamError, DexterError)

    def test_the_token_kinds_follow_the_enum_convention(self) -> None:
        assert all(kind.value == kind.name for kind in TokenKind)
        assert describe_token_kind(TokenKind.ACCESS) == "TokenKind.ACCESS"

    def test_the_middleware_lives_in_the_api_adapter(self) -> None:
        """Deliberately not re-exported from `dexter.iam`, which names no transport."""
        assert not hasattr(dexter.iam, "AuthenticationMiddleware")
        assert AuthenticationMiddleware.__module__.startswith("dexter.iam.api")

    def test_the_adapter_exports_its_own_surface(self) -> None:
        assert {Authentication, AuthenticationRegistry, AuthenticationRequirement}
        assert {use_authentication, require_authentication}
        assert {current_authentication, current_principal}
        assert {HEADER, SCHEME, STATE_KEY, UNAUTHORIZED}

    def test_the_requirements_follow_the_enum_convention(self) -> None:
        assert all(rule.value == rule.name for rule in AuthenticationRequirement)
        assert (
            describe_requirement(AuthenticationRequirement.ANONYMOUS)
            == "AuthenticationRequirement.ANONYMOUS"
        )

    def test_importing_the_core_does_not_import_the_api_module(self) -> None:
        """A worker minting tokens for a queue pulls in no routing."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.iam; print('dexter.api' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.iam pulled in dexter.api; the seam has been broken"
        )


class TestNotificationSurface:
    def test_the_message_types_are_exported(self) -> None:
        assert {Email, EmailBody, EmailBodyType, EmailNotifier}

    def test_the_shipped_double_and_its_topology_are_exported(self) -> None:
        assert {RecordingEmailNotifier, use_recording_notification}

    def test_every_error_is_exported(self) -> None:
        # No registration tree: this module owns no registry, so nothing about wiring it can
        # be got wrong that the container does not already refuse.
        errors = {NotificationError, DeliveryError}
        assert all(issubclass(error, NotificationError) for error in errors)
        assert issubclass(NotificationError, DexterError)

    def test_the_body_types_follow_the_enum_convention(self) -> None:
        assert all(kind.value == kind.name for kind in EmailBodyType)
        assert describe_body_type(EmailBodyType.TEXT) == "EmailBodyType.TEXT"

    def test_there_is_no_bare_use_notification(self) -> None:
        """One `use_*` per engine. A topology switch that registered nothing would be scenery."""
        assert not hasattr(dexter.notification, "use_notification")

    def test_the_engine_lives_in_its_own_package(self) -> None:
        assert not hasattr(dexter.notification, "ResendEmailNotifier")
        assert ResendEmailNotifier.__module__.startswith("dexter.notification.resend")
        assert {ResendConfig, use_resend_notification, register_resend_config}
        assert ENDPOINT == "/emails"
        assert set(RESEND_FIELD) == set(EmailBodyType)

    def test_importing_the_core_does_not_import_an_http_client(self) -> None:
        """What makes `httpx` an optional extra rather than a dependency."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.notification; print('httpx' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.notification pulled in httpx; the seam has been broken"
        )


class TestAwsSurface:
    def test_the_clients_are_exported(self) -> None:
        assert {
            S3Client,
            DynamoDbClient,
            SecretsManagerClient,
            ParameterStoreClient,
            SesClient,
            SnsClient,
            SqsClient,
        }

    def test_the_session_and_its_configuration_are_exported(self) -> None:
        assert {AwsSession, AwsConfig, AwsEndpoints, RetryMode}

    def test_the_value_contract_and_its_implementations_are_exported(self) -> None:
        # The provider pattern: a marker the application declares, three ways to satisfy it.
        assert {ValueSource, StaticValue, SecretValue, ParameterValue}

    def test_the_wiring_entry_points_are_exported(self) -> None:
        assert {
            use_aws,
            register_aws_config,
            register_secret_value,
            register_parameter_value,
        }

    def test_the_answer_shapes_are_exported(self) -> None:
        assert {ObjectSummary, ObjectPage, DeleteReport, DeleteFailure}
        assert {ReceivedMessage, OutboundMessage, MessageAttribute}
        assert {BatchResult, BatchSuccess, BatchFailure}
        assert {ItemPage, PutRequest, DeleteRequest, TransactGet}
        assert {Item, ItemKey, WriteRequest, TransactWrite}
        assert {TransactPut, TransactUpdate, TransactDelete, TransactConditionCheck}

    def test_the_condition_vocabulary_is_exported(self) -> None:
        # Named `Key` in `dexter.aws`, and imported here as `DynamoKey` because
        # `dexter.dependency_injection` already owns the plain name in this file.
        assert {Attr, DynamoKey, Condition}
        assert {Comparison, Between, BeginsWith, Contains, Exists, NotExists}
        assert {In, AttributeType, And, Or, Not}

    def test_the_streams_and_the_cache_are_exported(self) -> None:
        # A consumer annotating what `list_objects` or `query` returns needs these.
        assert {ItemStream, TtlCache}

    def test_every_error_is_exported(self) -> None:
        errors = {
            AwsError,
            AwsRequestError,
            AwsWiringError,
            AccessDeniedError,
            ThrottledError,
            CredentialsUnavailableError,
            ResourceNotFoundError,
            ObjectNotFoundError,
            SecretNotFoundError,
            ParameterNotFoundError,
            ItemEncodingError,
            ConditionFailedError,
            TransactionConflictError,
            BatchIncompleteError,
            EmailRejectedError,
            MessageTooLargeError,
        }
        assert all(issubclass(error, AwsError) for error in errors)
        assert issubclass(AwsError, DexterError)

    def test_the_retryable_failures_are_reachable_as_request_errors(self) -> None:
        # Both are under `AwsRequestError`, so an existing `except AwsRequestError` keeps
        # catching them. `ConditionFailedError` deliberately is not: nothing was wrong with
        # the request.
        assert issubclass(ThrottledError, AwsRequestError)
        assert issubclass(AccessDeniedError, AwsRequestError)
        assert not issubclass(ConditionFailedError, AwsRequestError)

    def test_the_enums_follow_the_enum_convention(self) -> None:
        for enum in (RetryMode, SmsType, ComparisonOperator):
            assert all(member.value == member.name for member in enum)
        assert describe_retry_mode(RetryMode.STANDARD) == "RetryMode.STANDARD"
        assert describe_sms_type(SmsType.TRANSACTIONAL) == "SmsType.TRANSACTIONAL"
        assert (
            describe_comparison_operator(ComparisonOperator.EQUALS)
            == "ComparisonOperator.EQUALS"
        )

    def test_no_boto3_type_reaches_the_public_surface(self) -> None:
        """The module's central promise. See `tests/aws/test_boundaries.py` for the rest."""
        for name in dir(dexter.aws):
            if name.startswith("_"):
                continue
            module = getattr(getattr(dexter.aws, name), "__module__", "")
            assert not module.startswith(("boto3", "botocore", "mypy_boto3")), name

    def test_the_notifier_seam_lives_in_the_notification_module(self) -> None:
        """`dexter.aws` names no other dexter module; the adapter points the other way."""
        assert not hasattr(dexter.aws, "SesEmailNotifier")
        assert SesEmailNotifier.__module__.startswith("dexter.notification.ses")
        assert use_ses_notification is not None
