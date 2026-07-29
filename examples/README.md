# The reference application

One runnable service, `storefront/`, composed from modules. It is the thing to copy when
starting a service on dexter: take the directory, delete the two modules, write your own.

```bash
./dx refapp worker      # run it as a worker, and read the transcript
./dx refapp web         # run it as a web service, and poke it
```

`./dx` with no arguments opens a menu with both. `./dx refapp worker --section <name>` runs
one part of the transcript, and the worker is also `uv run python -m examples.storefront`,
which is what CI smoke-runs.

It is a **demonstration, not a test**. It asserts nothing and no test executes it. It is
type-checked under full mypy strict and linted like the rest of the repository, which is its
only protection against drifting out of step with the API — and evidence that a consumer's code
can type-check too. It is not shipped in the wheel.

## What to read, in order

| File | Holds |
| --- | --- |
| [`application.py`](./storefront/application.py) | `MODULES`, and `build_container`. **Read this first** — it is nine lines and it is the whole composition root |
| `modules/<name>/use.py` | Everything that module contributes. The shape you copy |
| `modules/<name>/domain.py` | Its messages and the contracts it depends on. Mentions no framework |
| `modules/<name>/handlers.py` | Its handlers: the application core, then the API edge |
| `modules/<name>/services.py` | What implements the contracts |
| `__main__.py` | The worker walkthrough |

## Modules

A **module** is one capability — a domain, its handlers, its routes, the services they need —
registered by one function:

```python
def use_orders(builder: ContainerBuilder) -> None:
    """Everything the orders module contributes."""
    builder.register(Orders).to(InMemoryOrders, scope=Scope.SINGLETON)
    register_command_handler(builder, PlaceOrder, PlaceOrderHandler, scope=Scope.TRANSIENT)
    register_error(builder, NoSuchOrderError, status=HTTPStatus.NOT_FOUND, ...)
    register_handler(builder, PlaceOrderApi, HttpExposure(...), scope=Scope.TRANSIENT)
```

An application is a list of them, and adding a capability is a package plus one line:

```python
MODULES = (use_catalogue, use_orders)
```

**A module never calls `use_cqrs` or `use_api`.** Both bind unconditionally, so the second
module to call one would fail on a duplicate registration. `use_application` calls them once,
before any module — which is why `dexter.application` exists at all.

**Modules do not import each other.** `orders` prices an order against the catalogue by asking
for `Catalogue`, the contract that module declared — not for anything that implements it. So
registration order does not matter (swap the two entries in `MODULES` and nothing changes), and
leaving a module out is reported where the dependency is needed:

```
examples.storefront.modules.catalogue.domain.Catalogue is not registered in this container.

resolution chain:
examples.storefront.modules.orders.handlers.PlaceOrderHandler
  -> examples.storefront.modules.catalogue.domain.Catalogue (parameter 'catalogue')
```

## One service, two ways to run it

The same `build_container()` both times. What differs is what happens next — which is what a
module list buys: one description of the service, and no second list to keep in step.

| | Command | What it does |
| --- | --- | --- |
| Worker | `./dx refapp worker` | Resolves the buses, dispatches, prints, exits |
| Web | `./dx refapp web` | Hands the container to `create_app` and binds a socket |

`./dx refapp web` prints its address and a live request log; `/docs` drives every module's
routes from a browser, grouped by the tag each module registered them under. Both are ordinary
commands, so both are interruptible — Ctrl+C in the menu raises a confirm modal and lands you
back in the CLI with the port released.

### What the worker transcript shows

Each section shows one thing a signature cannot tell you.
`./dx refapp worker --section <name>` runs one on its own — the menu offers them as a
picker.

```
one module using another, through a contract
  ordered   -> 3 x DX-200
  priced at -> 1350p, read from the catalogue when placed
  stock now -> 0

leaving a scope settles what was dispatched in it
  inside the scope  -> queued 0
  after the scope   -> queued 1
```

The second block is the one to understand. `PlaceOrderHandler` published `OrderPlaced` and
returned; nobody awaited the reaction. Leaving the scope settled it — which is also why an HTTP
response is not built until the work its request started has finished.

The `missing` section deliberately builds an application without `catalogue`, so the error a
forgotten module produces is on screen rather than described.
