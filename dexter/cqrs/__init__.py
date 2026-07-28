"""Commands, queries, events, and the buses that carry them.

Wire the module into a container, then resolve a bus from a scope::

    builder = ContainerBuilder()
    use_cqrs(builder)
    register_command_handler(
        builder, CreateUser, CreateUserHandler, scope=Scope.TRANSIENT
    )
    container = builder.build()

    async with container.scope() as scope:
        commands = await scope.resolve(CommandBus)
        ticket = commands.dispatch(CreateUser(email="a@b.c"))
        user_id = await ticket.result()

A command changes state and has exactly one handler. A query reads state, has exactly one
handler, and is answered inline. An event records that something happened and has any number
of handlers, which run concurrently.

Sending a command or publishing an event hands back a ticket: its id exists immediately, and
`await ticket.result()` redeems the outcome whenever the caller wants it — including never, in
which case the work still runs and any failure surfaces from `bus.drain()`.
"""

from .bus import MessageBus as MessageBus
from .command_bus import CommandBus as CommandBus
from .command_bus import InProcessCommandBus as InProcessCommandBus
from .dispatch import Dispatch as Dispatch
from .dispatch import EventDispatch as EventDispatch
from .errors import BusClosedError as BusClosedError
from .errors import CqrsError as CqrsError
from .errors import CqrsGroupError as CqrsGroupError
from .errors import CqrsNotWiredError as CqrsNotWiredError
from .errors import CqrsRegistrationError as CqrsRegistrationError
from .errors import CqrsStateError as CqrsStateError
from .errors import DispatchError as DispatchError
from .errors import DispatchFailedError as DispatchFailedError
from .errors import DuplicateHandlerError as DuplicateHandlerError
from .errors import DuplicateMiddlewareError as DuplicateMiddlewareError
from .errors import EventHandlingError as EventHandlingError
from .errors import HandlerResultMismatchError as HandlerResultMismatchError
from .errors import InvalidHandlerError as InvalidHandlerError
from .errors import UnhandledCommandError as UnhandledCommandError
from .errors import UnhandledMessageError as UnhandledMessageError
from .errors import UnhandledQueryError as UnhandledQueryError
from .errors import UnparameterizedMessageError as UnparameterizedMessageError
from .event_bus import EventBus as EventBus
from .event_bus import InProcessEventBus as InProcessEventBus
from .models import Command as Command
from .models import CommandHandler as CommandHandler
from .models import Envelope as Envelope
from .models import Event as Event
from .models import EventHandler as EventHandler
from .models import Message as Message
from .models import MessageId as MessageId
from .models import Middleware as Middleware
from .models import Next as Next
from .models import Query as Query
from .models import QueryHandler as QueryHandler
from .models import new_message_id as new_message_id
from .pipeline import MiddlewarePipeline as MiddlewarePipeline
from .query_bus import InProcessQueryBus as InProcessQueryBus
from .query_bus import QueryBus as QueryBus
from .registry import CommandRegistry as CommandRegistry
from .registry import EventRegistry as EventRegistry
from .registry import QueryRegistry as QueryRegistry
from .use import register_command_handler as register_command_handler
from .use import register_event_handler as register_event_handler
from .use import register_middleware as register_middleware
from .use import register_query_handler as register_query_handler
from .use import use_cqrs as use_cqrs
