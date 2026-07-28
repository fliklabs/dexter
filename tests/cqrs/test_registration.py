"""Registration: what a handler must look like, and what is rejected while wiring."""

from typing import Any

import pytest

from dexter.cqrs import (
    Command,
    CommandRegistry,
    CqrsNotWiredError,
    DuplicateHandlerError,
    Event,
    EventRegistry,
    HandlerResultMismatchError,
    InvalidHandlerError,
    QueryRegistry,
    UnparameterizedMessageError,
    register_command_handler,
    register_event_handler,
    register_query_handler,
)
from dexter.dependency_injection import (
    ContainerBuilder,
    DuplicateRegistrationError,
    Scope,
)

from .conftest import (
    CountUsers,
    CountUsersHandler,
    CreateUser,
    CreateUserHandler,
    Explode,
    ExplodeHandler,
    GetUser,
    GetUserHandler,
    Greeter,
    Ledger,
    NobodyCares,
    RecordFirst,
    RecordSecond,
    UserCreated,
)


class Unparameterized(Command):  # type: ignore[type-arg]
    """A command that never says what it produces."""


class WrongResultHandler:
    async def handle(self, command: CreateUser) -> str:
        return "not an int"


class SubclassResultHandler:
    async def handle(self, command: CreateUser) -> bool:
        return True


class SynchronousHandler:
    def handle(self, command: CreateUser) -> int:
        return 7


async def _handle_without_annotations(self: Any, command: Any) -> Any:
    return 7


# Built dynamically because a genuinely unannotated `handle` cannot be written here: mypy
# strict rejects the definition before the registration check ever sees it.
_handle_without_annotations.__annotations__ = {}
UnannotatedHandler: Any = type(
    "UnannotatedHandler", (), {"handle": _handle_without_annotations}
)


class NoHandleMethod:
    pass


class NoisyEventHandler:
    async def handle(self, event: UserCreated) -> int:
        return 1


class DeepCreateUser(CreateUser):
    """A subclass of a concrete command; its result is still discoverable."""


class DeepHandler:
    async def handle(self, command: DeepCreateUser) -> int:
        return 7


class TestHappyPath:
    def test_registers_a_command_and_its_handler(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        registry = builder.resolve_instance(CommandRegistry)

        assert registry.resolve(CreateUser) is CreateUserHandler

    def test_accepts_a_handler_returning_a_subclass_of_the_declared_result(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, SubclassResultHandler, scope=Scope.TRANSIENT
        )

        assert builder.resolve_instance(CommandRegistry).is_registered(CreateUser)

    def test_reads_the_result_of_a_command_declared_on_a_base_class(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, DeepCreateUser, DeepHandler, scope=Scope.TRANSIENT
        )

        assert builder.resolve_instance(CommandRegistry).is_registered(DeepCreateUser)

    def test_an_event_takes_several_handlers_in_registration_order(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        register_event_handler(
            builder, UserCreated, RecordSecond, scope=Scope.TRANSIENT
        )

        registry = builder.resolve_instance(EventRegistry)
        assert registry.resolve(UserCreated) == (RecordFirst, RecordSecond)

    def test_an_event_with_no_handlers_resolves_to_nothing(
        self, builder: ContainerBuilder
    ) -> None:
        registry = builder.resolve_instance(EventRegistry)

        assert registry.resolve(UserCreated) == ()
        assert registry.is_registered(UserCreated) is False


class TestDuplicates:
    def test_raises_when_a_command_is_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        with pytest.raises(DuplicateHandlerError, match="exactly one handler"):
            register_command_handler(
                builder, CreateUser, SubclassResultHandler, scope=Scope.TRANSIENT
            )

    def test_raises_when_a_query_is_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        register_query_handler(builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT)
        with pytest.raises(DuplicateHandlerError, match="exactly one handler"):
            register_query_handler(
                builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT
            )

    def test_raises_when_the_same_event_handler_is_registered_twice(
        self, builder: ContainerBuilder
    ) -> None:
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        with pytest.raises(DuplicateHandlerError, match="would run twice"):
            register_event_handler(
                builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT
            )

    def test_raises_when_one_handler_class_serves_two_messages(
        self, builder: ContainerBuilder
    ) -> None:
        """Its `scope=` would be ambiguous, so the container refuses the second binding."""
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        # `RecordFirst` handles `UserCreated`, so this is a type error too; the point here
        # is which *runtime* guard trips.
        reused: Any = RecordFirst
        with pytest.raises(DuplicateRegistrationError):
            register_event_handler(builder, NobodyCares, reused, scope=Scope.SCOPED)


class TestResultAgreement:
    def test_raises_when_the_handler_returns_the_wrong_type(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(HandlerResultMismatchError, match="declares a result of"):
            register_command_handler(
                builder, CreateUser, WrongResultHandler, scope=Scope.TRANSIENT
            )

    def test_the_mismatch_names_both_sides(self, builder: ContainerBuilder) -> None:
        with pytest.raises(HandlerResultMismatchError) as caught:
            register_command_handler(
                builder, CreateUser, WrongResultHandler, scope=Scope.TRANSIENT
            )

        assert caught.value.declared is int
        assert caught.value.returned is str

    def test_raises_when_an_event_handler_returns_something(
        self, builder: ContainerBuilder
    ) -> None:
        noisy: Any = NoisyEventHandler
        with pytest.raises(HandlerResultMismatchError):
            register_event_handler(builder, UserCreated, noisy, scope=Scope.TRANSIENT)

    def test_raises_when_the_command_never_declared_a_result(
        self, builder: ContainerBuilder
    ) -> None:
        unparameterized: Any = Unparameterized
        handler: Any = CountUsersHandler
        with pytest.raises(UnparameterizedMessageError, match="does not say what"):
            register_command_handler(
                builder, unparameterized, handler, scope=Scope.TRANSIENT
            )

    def test_accepts_a_command_declaring_no_result(
        self, builder: ContainerBuilder
    ) -> None:
        register_command_handler(
            builder, Explode, ExplodeHandler, scope=Scope.TRANSIENT
        )

        assert builder.resolve_instance(CommandRegistry).is_registered(Explode)


class TestHandlerShape:
    def test_raises_when_handle_is_not_asynchronous(
        self, builder: ContainerBuilder
    ) -> None:
        synchronous: Any = SynchronousHandler
        with pytest.raises(InvalidHandlerError, match="not asynchronous"):
            register_command_handler(
                builder, CreateUser, synchronous, scope=Scope.TRANSIENT
            )

    def test_raises_when_there_is_no_handle_method(
        self, builder: ContainerBuilder
    ) -> None:
        handler_less: Any = NoHandleMethod
        with pytest.raises(InvalidHandlerError, match="no `handle` method"):
            register_command_handler(
                builder, CreateUser, handler_less, scope=Scope.TRANSIENT
            )

    def test_raises_when_handle_has_no_return_annotation(
        self, builder: ContainerBuilder
    ) -> None:
        with pytest.raises(InvalidHandlerError, match="no return annotation"):
            register_command_handler(
                builder, CreateUser, UnannotatedHandler, scope=Scope.TRANSIENT
            )

    def test_raises_when_the_handler_is_a_protocol(
        self, builder: ContainerBuilder
    ) -> None:
        protocol: Any = Greeter
        with pytest.raises(InvalidHandlerError, match="Protocol"):
            register_command_handler(
                builder, CreateUser, protocol, scope=Scope.TRANSIENT
            )

    def test_raises_when_the_handler_is_not_a_class(
        self, builder: ContainerBuilder
    ) -> None:
        not_a_class: Any = "CreateUserHandler"
        with pytest.raises(InvalidHandlerError, match="must be a class"):
            register_command_handler(
                builder, CreateUser, not_a_class, scope=Scope.TRANSIENT
            )


class TestWiringOrder:
    def test_raises_when_use_cqrs_was_never_called(
        self, bare_builder: ContainerBuilder
    ) -> None:
        with pytest.raises(CqrsNotWiredError, match="use_cqrs"):
            register_command_handler(
                bare_builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
            )

    def test_the_message_names_the_call_that_is_missing(
        self, bare_builder: ContainerBuilder
    ) -> None:
        bare_builder.register(Ledger).to_instance(Ledger())
        with pytest.raises(CqrsNotWiredError) as caught:
            register_query_handler(
                bare_builder, CountUsers, CountUsersHandler, scope=Scope.TRANSIENT
            )

        assert "use_cqrs(builder)" in str(caught.value)


class TestRegistryIntrospection:
    def test_lists_every_command_registration(self, builder: ContainerBuilder) -> None:
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        registry = builder.resolve_instance(CommandRegistry)

        assert registry.registrations() == ((CreateUser, CreateUserHandler),)

    def test_lists_every_event_registration(self, builder: ContainerBuilder) -> None:
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)
        registry = builder.resolve_instance(EventRegistry)

        assert registry.registrations() == ((UserCreated, (RecordFirst,)),)

    def test_lists_every_query_registration(self, builder: ContainerBuilder) -> None:
        register_query_handler(builder, GetUser, GetUserHandler, scope=Scope.TRANSIENT)
        registry = builder.resolve_instance(QueryRegistry)

        assert registry.registrations() == ((GetUser, GetUserHandler),)


class TestExactTypeLookup:
    def test_a_subclass_does_not_inherit_its_parents_handler(
        self, builder: ContainerBuilder
    ) -> None:
        """Lookup is on the exact runtime class, so a subclass needs its own registration."""
        register_command_handler(
            builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
        )
        registry = builder.resolve_instance(CommandRegistry)

        assert registry.is_registered(DeepCreateUser) is False


class TestEventBaseIsNotParameterized:
    def test_an_event_needs_no_result_declaration(
        self, builder: ContainerBuilder
    ) -> None:
        assert issubclass(UserCreated, Event)
        register_event_handler(builder, UserCreated, RecordFirst, scope=Scope.TRANSIENT)

        assert builder.resolve_instance(EventRegistry).is_registered(UserCreated)
