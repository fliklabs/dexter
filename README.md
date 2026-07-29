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

## Install

```bash
uv add "dexter @ git+https://github.com/fliklabs/dexter"
```

Runtime dependencies: `pydantic>=2.12`, `click>=8.3` and `rich>=14`. The pydantic floor is
hard — earlier releases have no cp314 wheel and fail to build on Python 3.14. `click` and
`rich` are what `dexter.cli` is built from; modules are imported directly, so a consumer who
never touches the CLI never imports them.

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

Scope semantics are hard to picture from signatures, so there is a runnable reference app:

```bash
uv run python -m examples.taskflow
```

It handles several jobs concurrently, one container scope each, and labels every instance so you
can see the singleton shared across all of them, the scoped instance rebuilt per scope, and the
transient differing on every resolve. See [examples/README.md](./examples/README.md).

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

An order service that places orders, fans an event out to two reactions, correlates what a
handler publishes with the command that caused it, dispatches without redeeming, and shows what
an aggregated failure reports. See [examples/README.md](./examples/README.md).

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

Navigation is stdlib `curses`, imported lazily so the module still works where it is absent.

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

A change is not finished until `./verify.sh` exits 0.

See [AGENTS.md](./AGENTS.md) for repository structure and conventions.

## Licence

MIT — see [LICENSE](./LICENSE).
