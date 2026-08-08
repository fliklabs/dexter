# dexter

A collection of independent, reusable application framework modules.

Each module under `dexter/` is self-contained and imported directly:

```python
from dexter.dependency_injection import Container
```

`import dexter` deliberately pulls in nothing but the version — importing the top
level never drags in every framework.

dexter is **async-native**: nothing exposes a synchronous entry point, and nothing
drives an event loop on your behalf.

## Status

| Module | Status |
| --- | --- |
| `dexter.commons` | Scaffolded — shared primitives |
| `dexter.dependency_injection` | Implemented — container, scopes, async resolution |
| `dexter.cqrs` | Implemented — commands, queries, events, buses, middleware |
| `dexter.cli` | Implemented — a keyboard-navigable CLI you register commands into |
| `dexter.api` | Implemented — typed request handlers, served over HTTP |
| `dexter.iam` | Implemented — magic-code login, JWT access and refresh tokens, per-route authentication |
| `dexter.notification` | Implemented — sending email through a contract, with Resend and SES engines |
| `dexter.aws` | Implemented — S3, DynamoDB, Secrets Manager, SSM, SES, SNS and SQS |
| `dexter.application` | Implemented — composing a service from modules |

## Install

```bash
uv add "dexter @ git+https://github.com/fliklabs/dexter"
```

Runtime dependencies: `pydantic`, `click`, `rich`, `fastapi`, `pyjwt` and `boto3`. Three floors
are hard: earlier pydantic has no cp314 wheel and fails to build on Python 3.14, pydantic models
as query parameters arrived in fastapi 0.115, and pyjwt gained `py.typed` in 2.0. `click` and
`rich` are what `dexter.cli` is built from, `fastapi` what `dexter.api.http` is built from,
`pyjwt` what `dexter.iam` signs with, and `boto3` what `dexter.aws` talks to AWS with; modules
are imported directly, so a consumer who never touches one never imports it.

Sending email through Resend needs an HTTP client, which is an **extra** rather than something
every consumer inherits:

```bash
uv add "dexter[resend] @ git+https://github.com/fliklabs/dexter"
```

Note that importing a framework module costs roughly 45 ms, because pydantic's schema
machinery loads when the first model is defined. `import dexter` on its own stays free.

## Dependency injection

Wire an application with a `ContainerBuilder`, then resolve from the `Container` it
builds:

```python
from dexter.dependency_injection import ContainerBuilder, Scope

builder = ContainerBuilder()
builder.register(Repository).to(SqlRepository, scope=Scope.SCOPED)
builder.register(Pool).to(open_pool, scope=Scope.SINGLETON)  # async factory
builder.register(Clock).to_instance(SystemClock())

container = builder.build()

async with container.scope() as scope:
    handler = await scope.resolve(Handler)
```

Bindings are two calls so that a type checker can verify them — see below. `scope` is
required: lifetime is too consequential to be chosen by omission.

| Scope | Lifetime |
| --- | --- |
| `Scope.TRANSIENT` | A new instance for every resolution |
| `Scope.SINGLETON` | One instance permeating every scope |
| `Scope.SCOPED` | One instance spanning every resolution within one scope |

**A dependency must live at least as long as whatever depends on it.** In practice that means a
`Singleton` may not depend on a `Scoped` key — it would outlive the scope and capture one scope's
instance for everyone. `build()` rejects that with `CaptiveDependencyError`, so it surfaces while
wiring rather than as inexplicably shared state later. For the same reason a `Scoped` key cannot
be resolved from the root, which is not a scope; that raises `ScopeRequiredError`.

Dependencies are discovered from constructor annotations and passed by keyword. A
parameter annotated `X | None` resolves to `None` when `X` is unregistered, and a
parameter annotated `Container` receives the container that is doing the resolving.
Resolving an unregistered type raises rather than constructing it implicitly, so a typo
fails loudly instead of silently producing an object.

A `Container` belongs to one event loop. Concurrent resolutions of the same key yield
exactly one instance, and cancelling one resolver does not cancel construction for the
others.

**Closing releases what the container created.** Pass `dispose=` and it is called with the
instance when the container or scope that built it closes, in reverse creation order — so a
dependency is never released before whatever depends on it:

```python
builder.register(Pool).to(open_pool, scope=Scope.SINGLETON, dispose=Pool.aclose)
```

It is explicit rather than inferred from whatever `aclose`-shaped method a type happens to
have, because that guess is wrong as often as it is right. Every callback runs even if an
earlier one fails; the failures are raised together as `DisposalError`. A `Scope.TRANSIENT`
binding cannot take one — nothing is kept, so it could only ever be a silent no-op — and
neither can `to_instance`, since you built that object and still hold it.

### See it working

```bash
uv run python -m examples.storefront --section settling
```

The reference service, run as a worker. Scope semantics are hard to picture from a signature,
so the transcript shows a scope being left and the work started inside it finishing first. See
[examples/README.md](./examples/README.md).

## For consumers using mypy

`dexter` ships type information (PEP 561), so its types are visible to your type checker
with no stubs required.

Registration and resolution accept abstract classes and `Protocol`s directly — **no
`type-abstract` suppression is needed** on your side:

```python
# Repository is an ABC; SqlRepository implements it.
builder.register(Repository).to(SqlRepository, scope=Scope.SCOPED)

# Inferred as Repository, with no suppression at the call site.
repository = await container.resolve(Repository)
```

Binding is deliberately two calls because that is what makes the provider checkable.
mypy will reject a provider that cannot produce the key:

```python
builder.register(Repository).to(returns_an_int, scope=Scope.TRANSIENT)
# error: Argument 1 to "to" of "Binder" has incompatible type "Callable[[], int]";
#        expected "type[Repository] | Callable[..., Repository]
#                  | Callable[..., Awaitable[Repository]]"  [arg-type]
```

Collapsing that into one call would mean widening the key so abstract types are
accepted, and that widening makes mypy infer the type variable as `object` — silently
accepting the wrong provider. Two calls buys the check.

## CQRS

Commands change state, queries read it, events announce that something happened. Each is a
frozen pydantic model; the type parameter says what a handler produces.

```python
from dexter.cqrs import Command, CommandBus, Event, Query, use_cqrs


class PlaceOrder(Command[OrderId]):
    sku: str
    quantity: int


class OrderPlaced(Event):
    order_id: str


class PlaceOrderHandler:
    def __init__(self, orders: OrderBook, events: EventBus) -> None:
        self.orders, self.events = orders, events

    async def handle(self, command: PlaceOrder) -> OrderId:
        order_id = await self.orders.place(command.sku, command.quantity)
        self.events.publish(OrderPlaced(order_id=order_id.value))
        return order_id
```

A handler inherits nothing from dexter: it is a class with one async `handle`, whose
dependencies arrive the same way any other class's do. Wire it with the module's `use.py`:

```python
builder = ContainerBuilder()
use_cqrs(builder)
register_command_handler(builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT)
register_event_handler(builder, OrderPlaced, ReserveStock, scope=Scope.TRANSIENT)
container = builder.build()

async with container.scope() as scope:
    commands = await scope.resolve(CommandBus)
    order_id = await commands.dispatch(PlaceOrder(sku="DX-100", quantity=2)).result()
```

**Sending hands back a ticket.** `dispatch` and `publish` return immediately with an id you
can log or correlate, and `await ticket.result()` redeems the outcome whenever you want it —
typed by the message, so `order_id` above is an `OrderId` and not `Any`. Never redeeming a
ticket is a valid choice: the work still runs, and leaving the scope waits for it. Queries are
the exception and are answered inline, since a read has nothing worth deferring.

| Message | Handlers | Sending it |
| --- | --- | --- |
| `Command[TResult]` | Exactly one | `bus.dispatch(command) -> Dispatch[TResult]` |
| `Query[TResult]` | Exactly one | `await bus.ask(query) -> TResult` |
| `Event` | Any number, concurrent | `bus.publish(event) -> EventDispatch` |

Identity lives on an envelope built when the message is sent, never on the message itself — so
two equal commands are equal, and dispatching one twice yields two distinguishable dispatches
carrying `id`, `correlation_id` and `causation_id`.

An event's handlers all run, whatever the others do; every failure arrives together as
`EventHandlingError`, an `ExceptionGroup` you can split with `except*`. Publishing an event
nobody handles is not an error, and the ticket's `handler_count` says so.

Middleware wraps every dispatch on every bus, in registration order, outermost first:

```python
class Tracing:
    async def handle(self, envelope: Envelope[Any], call_next: Next) -> Any:
        return await call_next(envelope)


register_middleware(builder, Tracing, scope=Scope.SCOPED)
```

**Leaving a scope settles its buses.** `use_cqrs` binds them so that the scope waits for every
dispatch started in it and then reports anything that failed and was never redeemed. Nothing to
remember, and no window in which a handler is still resolving from a scope that has closed:

```python
async with container.scope() as scope:
    commands = await scope.resolve(CommandBus)
    commands.dispatch(PlaceOrder(sku="DX-100", quantity=2))  # ticket dropped on purpose
# the handler has run by here, and so has every event it published
```

That does mean leaving a scope blocks until its dispatches finish, in the same way
`asyncio.TaskGroup` does.

**Buses are `Scope.SCOPED`, always.** A bus resolves handlers from the container it holds, so a
singleton one would capture the root and bypass the scope it was asked for; resolving a bus
outside a scope raises `ScopeRequiredError`. A handler registered for the wrong message is a
type error, and one whose return type disagrees with its message is rejected when it is
registered, long before anything dispatches.

### See it working

```bash
uv run python -m examples.storefront
```

An order service: a command handed a ticket redeemed later, an event reaching a reaction nobody
awaited, and what dispatching something unwired reports. See
[examples/README.md](./examples/README.md).

## CLI

`dexter.cli` turns commands registered into a container into both a keyboard-driven menu and a
scriptable command tree. It **ships no commands of its own** — you register yours:

```python
import click

from dexter.cli import inject, register_command, run, use_cli
from dexter.dependency_injection import Container, ContainerBuilder


@click.command("deploy")
@click.option("--to", type=click.Choice(["staging", "production"]), required=True)
@inject
async def deploy(scope: Container, to: str) -> None:
    """Deploy the service."""
    releases = await scope.resolve(Releases)
    await releases.deploy(to)


builder = ContainerBuilder()
use_cli(builder)
register_command(builder, deploy)
container = builder.build()
```

`inject` opens a container scope for the call and closes it afterwards, so a command resolves
exactly like a request handler and whatever it resolved is released when it finishes.

Your entry point starts the loop — dexter never does:

```python
raise SystemExit(asyncio.run(run(container, sys.argv[1:], prog_name="mytool")))
```

**The menu is a shell over the same tree.** `run` parses arguments when it gets them, opens the
menu when it gets none and there is a terminal, and prints help when there is no terminal — so
CI, scripts and agents never touch the interactive layer, and a piped invocation cannot crash
inside curses.

| You type | You get |
| --- | --- |
| `mytool` | The menu: ↑↓ to move, Enter to select, ESC to go back |
| `mytool deploy --to staging` | The same command, scripted |
| `mytool --help` | Everything, generated from the same tree |
| `echo \| mytool` | Help, and exit 0 |

Picking a command in the menu builds a form from its options — flags toggle, a `Choice` becomes
a picker, everything else opens an inline editor — and then shows you the shell command it is
about to run, which is how the menu teaches its own scriptable form.

A command that keeps running is watched rather than awaited, so its pane stays live: ↑↓ and
PgUp/PgDn scroll back through the output while it is still arriving, dragging across it copies
what you dragged over — the view scrolls itself when you drag past an edge — and Ctrl+C asks
before it stops anything.

Navigation is stdlib `curses`, imported lazily so the module still works where it is absent.
Mouse reporting is asked for while the menu is up, which takes over the terminal's own
click-and-drag; most terminals give it back while Shift is held.

## API

`dexter.api` serves typed request handlers. A handler is an ordinary class with one async
method: it takes a pydantic model and returns one, and nothing about it says HTTP.

```python
from http import HTTPMethod

from dexter.api import HttpExposure, RequestContext, register_handler, use_api
from dexter.api.http import create_app
from dexter.dependency_injection import ContainerBuilder, Scope


class GetRoom(BaseModel):
    room_id: int
    verbose: bool = False


class GetRoomHandler:
    """Describe one room."""

    def __init__(self, rooms: RoomStore, context: RequestContext) -> None:
        self.rooms = rooms
        self.context = context

    async def handle(self, request: GetRoom) -> RoomView:
        tenant = self.context.headers.get("x-tenant")
        return await self.rooms.describe(request.room_id, tenant)


builder = ContainerBuilder()
use_api(builder)
register_handler(
    builder,
    GetRoomHandler,
    HttpExposure(method=HTTPMethod.GET, path="/rooms/{room_id}", tags=("rooms",)),
    scope=Scope.TRANSIENT,
)
container = builder.build()

app = await create_app(container)
```

`room_id` is read from the path because the path names it, and `verbose` from the query string
because nothing else claimed it. Both are validated, coerced and documented by the framework
underneath, so `/openapi.json` describes them with the constraints declared on the model.

**Headers and cookies arrive by injection, not as an argument.** `handle` takes one parameter,
exactly as a CQRS handler does; everything else about the invocation lives on a `RequestContext`
bound `Scope.SCOPED`. That matters most for the code that is not the handler — a repository
wanting the tenant, an audit service wanting the caller's address — because a container binding
is reachable from any depth by declaring a parameter:

```python
def current_tenant(context: RequestContext) -> Tenant:
    return Tenant(context.headers.get("x-tenant") or "anonymous")


builder.register(Tenant).to(current_tenant, scope=Scope.SCOPED)
# now anything in the graph asks for a `Tenant` and never mentions HTTP
```

The ambient half is a `contextvars.ContextVar`, never a thread-local: requests share the event
loop's thread, so a thread-local hands one caller's identity to another request.

**A request is one container scope, and it closes before the response is built.** So everything
registered with `dispose=` has finished by the time the caller is told anything — including,
for an application that also called `use_cqrs`, every command a handler dispatched and every
event those published. `dexter.api` never imports `dexter.cqrs` to arrange that; the container
does it.

**Middleware is resolved per request and sees no transport type.** It takes an `Invocation` —
the parsed request, the context, the handler class, the exposure — so one middleware applies
however a handler is reached. Not calling `call_next` refuses the request, and because
`Invocation.handler` is the class, a refused request never constructs the handler at all.

**Refuse by raising, not by returning**, whenever the middleware guards more than one handler.
Whatever it returns is still serialised through the response model of the route it refused, so
a value that suits one route is an invalid response on the next. A mapped exception produces
the response itself and therefore fits all of them:

```python
class RequireTenant:
    async def handle(self, invocation: Invocation, call_next: ApiNext) -> Any:
        if invocation.context.headers.get("x-tenant") is None:
            raise NotAuthenticatedError("say who you are")
        return await call_next(invocation)


register_api_middleware(builder, RequireTenant, scope=Scope.SCOPED)
register_error(builder, NotAuthenticatedError, status=HTTPStatus.UNAUTHORIZED)
```

`register_error` maps a domain exception — and its subclasses — to a status.

**Every failure answers in one shape**: `application/problem+json`, per RFC 9457. That holds
whichever layer failed, so a client needs one parser rather than one per kind of thing that can
go wrong:

| What failed | Status | Body |
| --- | --- | --- |
| A mapped domain exception | as registered | `{title, status, detail}` |
| Request validation | 422 | the same, plus `errors` naming each rejected field |
| A raised `HTTPException` | as raised | the same, keeping its detail and headers |
| Anything nobody anticipated | 500 | the same, saying nothing about itself |

The last row is deliberate twice over. **The body says nothing** — mapping an exception is your
statement that its message is safe to show, and `str()` of an unanticipated one can carry a
connection string. And **the exception still propagates**, so your logs and error tracker get
the full traceback: dexter renders the response from the outermost error handler, which sends
it and then re-raises. Answering a failure and silencing it are different things, and only
mapping does the second.

That is also why nothing is mapped for you — including `DisposalError`, the failure you get
when a handler returned cleanly but the work it dispatched did not settle. Pre-registering it
would produce a byte-identical response and cost you the traceback.

If you already installed your own exception handler, dexter leaves it alone.

| Where a field comes from | When |
| --- | --- |
| The path | The exposure's path names it |
| The query string | Nothing else claimed it, on a method without a body |
| The body | Nothing else claimed it, on `POST`, `PUT` or `PATCH` |
| Nowhere — it is injected | It is on `RequestContext`: headers, cookies, the client address |

**`create_app` lives in `dexter.api.http`, and importing `dexter.api` pulls in no web
framework.** That split is the seam a second protocol would use: an `Exposure` subclass, a
package beside `http/`, and the same handlers. A test walks the package and enforces it.

Your entry point starts the loop and owns the container — dexter never does:

```python
async def main() -> None:
    container = build_container()
    app = await create_app(container)
    try:
        await uvicorn.Server(uvicorn.Config(app)).serve()
    finally:
        await container.aclose()


asyncio.run(main())
```

`create_app` is a coroutine, because every read from a built container is one. So an application
object cannot be built at import time, and `uvicorn module:app` is not how this is served — the
six lines above are.

### See it working

```bash
./dx refapp web      # http://127.0.0.1:8000/docs
```

The reference service behind a socket: path, query and body binding, mapped failures as problem
details, and a container scope per request. The same container the worker builds. See
[examples/README.md](./examples/README.md).

## IAM

Who is calling. **Not** whether they are allowed to — there is no authorization here, no user
table, and no opinion about who may log in.

Two exchanges. A code goes to an address and comes back once:

```python
from dexter.iam import MagicCodeService, Principal, TokenService

code = await codes.issue(email)  # returned once; the store keeps only a digest
await notifier.send(Email(..., body=EmailBody.text(f"Your code is {code}.")))
...
await codes.verify(email, presented)  # raises, or consumes the code
pair = tokens.mint(Principal.of(email))  # access + refresh, with both expiry instants
```

and a token is read on the way back in:

```python
principal = tokens.verify_access(bearer)  # raises, or says who
```

Wiring is the usual two shapes — what the module provides, then what the application
contributes. The signing key is the application's, so it arrives through a `register_*`:

```python
from datetime import timedelta

from dexter.iam import (
    MagicCodePolicy,
    TokenPolicy,
    register_magic_code_policy,
    register_token_policy,
    use_iam,
    use_in_memory_magic_codes,
)
from dexter.iam.api import require_authentication, use_authentication

use_api(builder)  # or use_application(builder)
use_iam(builder)
use_in_memory_magic_codes(builder)
register_token_policy(
    builder,
    TokenPolicy(
        secret=settings.jwt_secret,  # 32 bytes or more; the model insists
        issuer="plum",
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(days=30),
    ),
)
register_magic_code_policy(builder, MagicCodePolicy(secret=settings.jwt_secret))
use_authentication(builder)  # registers the middleware first, and maps its 401s
```

### Which routes need a caller

**Default open.** A handler nobody names is anonymous; one call closes one handler, whatever its
exposures:

```python
register_handler(builder, PickupApi, HttpExposure(...), scope=Scope.TRANSIENT)
require_authentication(builder, PickupApi)
```

The rule is keyed on the handler class, which is what `Invocation` carries — so a refused
request is turned away **before** the container builds the handler or anything beneath it. A
request nobody is allowed to make costs no database connection.

`AuthenticationRegistry.requirements()` lists every rule, which is what makes the open default
auditable: an application that wants default-deny asserts over `ExposureRegistry.records()` that
every handler was named.

### Reaching the caller

Ask for one, the same way you ask for anything else:

```python
class WhoAmI:
    def __init__(self, principal: Principal) -> None:  # 401 without a caller
        self.principal = principal


class Greeting:
    def __init__(self, authentication: Authentication) -> None:  # works either way
        self.authentication = authentication
```

Declaring a `Principal` **is** the statement that the operation needs a caller — handler
dependencies are built inside the request's error handling, so it answers 401 rather than 500.
`Authentication` always resolves and may name nobody. Both are `Scope.SCOPED`, so a repository
three levels down can know who is asking without a web framework in its imports.

### What it does not do

Refresh is **stateless**: a refresh token is a self-contained JWT with an `exp` and nothing is
looked up, so logging out clears the client and the token stays valid until it expires. Every
token carries a `jti`, which is the handle a session store would key on when that changes.
There is no authorization, no user table, no whitelist — deciding whether an address may log in
is yours, and it belongs *before* the call to `issue`, so a rejected address costs neither a
stored record nor a sent message.

## Notification

One contract, and one line of wiring choosing who honours it:

```python
from dexter.notification import Email, EmailBody, EmailNotifier


class SendMagicCode:
    def __init__(self, notifier: EmailNotifier) -> None:  # names no provider
        self.notifier = notifier

    async def send(self, to: str, code: str) -> None:
        await self.notifier.send(
            Email(
                from_address="Plum <noreply@example.com>",
                to_addresses=(to,),
                subject="Your code",
                body=EmailBody.text(f"Your code is {code}."),
            )
        )
```

```python
# Records, sends nothing. Tests and local development.
use_recording_notification(builder)
```

```python
from dexter.notification.resend import (
    ResendConfig,
    register_resend_config,
    use_resend_notification,
)

use_resend_notification(builder)
register_resend_config(builder, ResendConfig(api_key=settings.resend_api_key))
```

```python
from dexter.notification.ses import use_ses_notification

use_ses_notification(builder)  # needs use_aws(builder); the region is AwsConfig's
```

Exactly one of those. Calling two binds `EmailNotifier` twice and the container refuses the
second — the right failure, because the alternative is real mail sent from a test suite.

`RecordingEmailNotifier` is bound under both keys, so a test resolves the concrete class and
reads `sent` back:

```python
recorder = await container.resolve(RecordingEmailNotifier)
assert recorder.last is not None
assert "Your code is" in recorder.last.body.data
```

dexter renders nothing — a subject and a body arrive composed, because templating is a choice
most consumers have already made. `httpx` is an extra, not a dependency: importing
`dexter.notification` pulls in no HTTP client — and no `boto3` either, though the SES engine is
right there. A test enforces both.

## AWS

Seven clients over one boto3 session. Every method is `async def`, and **no boto3 type appears on
any signature** — a `ClientError` never reaches your code, and neither does a `Binary` or a
`ConditionBase`:

```python
from dexter.aws import AwsConfig, DynamoDbClient, S3Client, register_aws_config, use_aws

use_aws(builder)
register_aws_config(builder, AwsConfig(region="ap-southeast-2"))
```

```python
class StorePhoto:
    def __init__(self, storage: S3Client) -> None:
        self.storage = storage

    async def upload_url(self, item_id: str) -> str:
        return await self.storage.presigned_put_url(
            BUCKET, f"items/{item_id}/photo.jpg", content_type="image/jpeg"
        )
```

Queries read as Python, and `Key` offers only the operators a key condition actually allows — so
`Key("sk").contains(...)` is a type error rather than a service error:

```python
from dexter.aws.dynamodb import Attr, Key

stream = orders.query(
    "orders",
    key_condition=Key("pk").equals("u#1") & Key("sk").begins_with("order#"),
    filter=Attr("status").not_equals("CANCELLED"),
)
async for order in stream:  # pages until there is genuinely nothing left
    ...
```

Nothing truncates: `list_objects`, `query`, `scan` and `get_parameters_by_path` all paginate, and
none takes a `max_keys` that silently caps. Batches never return `None` — S3, SQS and SNS answer
`200` with the refused entries in the body, and DynamoDB answers `200` with an `UnprocessedItems`
map, so partial failure comes back as a report and unprocessed writes are retried.

### Configuration values

A component depends on a **value**, never on the store it lives in, so the same code runs against
a settings file locally and Secrets Manager in production:

```python
from dexter.aws import StaticValue, ValueSource, register_secret_value


class DatabasePassword(ValueSource, Protocol):
    """The application's own marker for one configured value."""


# locally: no AWS account, no credentials, no network
builder.register(DatabasePassword).to_instance(StaticValue("hunter2"))

# deployed
register_secret_value(
    builder,
    DatabasePassword,
    secret_id="app/production/secrets",
    secret_key="DATABASE_PASSWORD",
    scope=Scope.SINGLETON,
)
```

The value is fetched at first use rather than at wiring time — so `build_container()` still runs
in CI with no credentials — and cached, with one in-flight fetch per secret however many callers
arrive at once. `SecretValue`'s `repr` names the location and never the value.

## Application

A **module** is one capability of a service — a domain, its handlers, its routes, the services
they need — registered by one function:

```python
from dexter.application import register_module, use_application


def use_orders(builder: ContainerBuilder) -> None:
    """Everything the orders module contributes."""
    builder.register(Orders).to(InMemoryOrders, scope=Scope.SINGLETON)
    register_command_handler(
        builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT
    )
    register_error(builder, NoSuchOrderError, status=HTTPStatus.NOT_FOUND)
    register_handler(builder, PlaceOrderApi, HttpExposure(...), scope=Scope.TRANSIENT)
```

An application is a list of them:

```python
MODULES = (use_catalogue, use_orders)


def build_container() -> Container:
    builder = ContainerBuilder()
    use_application(builder)
    for module in MODULES:
        register_module(builder, module)
    return builder.build()
```

`use_application` wires the CQRS registries and buses, the API registries, and the registry of
modules — **once, before any module**. That is the reason this module exists rather than being
a convention: `use_cqrs` and `use_api` bind unconditionally and a builder refuses a repeat, so
the second module to wire its own would fail on a duplicate registration naming an internal
type. Calling them here means a module cannot make that mistake, because it has nothing to
call.

**One list, however the service runs.** A web application hands the container to `create_app`;
a worker resolves the buses and processes work. They differ in what they *do* with the modules,
not in which modules they have — so nothing has to be kept in step, and a capability added for
one is reachable from the other by construction. The API registries are wired either way: a
module declares everything it offers, and an application decides which surfaces to expose.

**Modules do not import each other.** One that needs what another provides asks the container
for it by type — the *contract*, not the class implementing it. So registration order is
irrelevant, and a module is removed by deleting a line. Nothing declares or checks dependencies
between modules; leaving one out is reported when the dependency is resolved, with the chain
naming what was looked for and what asked for it.

```python
registry = await container.resolve(ModuleRegistry)
registry.names()  # ("use_catalogue", "use_orders")
```

## Development

Requires [uv](https://docs.astral.sh/uv/). Python is installed and pinned by uv —
you do not need a system Python.

```bash
uv sync                       # create .venv and install dev dependencies
./dx                          # the repo CLI: run an example, run the tests
./dx test                     # the suite, with pass rate, timing and coverage
./verify.sh --fix             # format, lint, type-check, test
./verify.sh                   # same, without writing changes
uv run pytest tests/cqrs      # run one module's tests
```

`./verify.sh` is the gate and is what CI runs; `./dx test` is the feedback loop and reports the
statistics the gate does not. `./dx verify` runs the gate.

The gate measures coverage and **fails below 90%**. A bare `uv run pytest` does not, so running
one module's tests stays fast.

A change is not finished until `./verify.sh` exits 0.

See [AGENTS.md](./AGENTS.md) for repository structure and conventions.

## Licence

MIT — see [LICENSE](./LICENSE).
