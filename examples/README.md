# Reference applications

Runnable programs that demonstrate dexter in use. They exist because some of the library's
behaviour cannot be seen by reading signatures — you cannot tell from a type that a singleton is
shared across scopes while a scoped instance is rebuilt per scope.

These are **demonstrations, not tests**. They assert nothing. They are type-checked under mypy
strict and linted like the rest of the repository, so they cannot silently drift out of step
with the API, but nothing in the test suite executes them.

They are not shipped in the wheel, so nothing here affects what a consumer installs.

Run one directly, or pick it from the repository CLI:

```bash
./dx example list             # what exists
./dx example storefront       # run it
./dx                          # or choose from the menu
```

Every walkthrough accepts `--section` to run one part of it on its own.

| Example | Shows |
| --- | --- |
| [`taskflow/`](./taskflow) | A simulated async job service: all three scopes, async factories, disposal, optional dependencies, self-injection, and what a resolution failure reports |
| [`storefront/`](./storefront) | A CQRS order service: typed commands, queries and events, tickets, correlation, middleware, deferred dispatch, scope settling, and aggregated failures |
| [`frontdesk/`](./frontdesk) | An HTTP API over a CQRS core: path, query and body binding, headers and cookies, injected identity, middleware, mapped failures, and a container scope per request |

## Serving all three at once

```bash
./dx serve            # http://127.0.0.1:8000/docs
```

The walkthroughs print; this one listens. `./dx serve` puts every example behind one address so
you can send it real requests from a browser — one Swagger UI covering all of them, with each
example under its own prefix:

| Prefix | What it shows over HTTP |
| --- | --- |
| `/taskflow` | `dexter.api` over plain dependency injection, with no CQRS anywhere. `GET /taskflow/scope` reports the singleton pool every request shares next to the scoped repository each request gets its own of |
| `/storefront` | Endpoints that are pure translation — request in, command or query onto a bus, result out. `POST /storefront/orders` returns what the warehouse had reserved *by the time the response was built*, which is the event settling before the caller is answered |
| `/frontdesk` | The full picture: headers, cookies, injected identity, middleware and mapped failures |

**They are three separate containers, not one.** `use_cqrs` binds its registries
unconditionally and a builder refuses a repeat, so two examples that both use CQRS could never
share one — and do not need to. `create_app` takes an application and a prefix, so each keeps
its own `wiring.py`, readable on its own, and the routes land side by side under one schema.

Ctrl+C stops it. From the menu that is a confirm modal and you land back in the CLI; from a
shell it is an ordinary interrupt.

`storefront` and `taskflow` grow their HTTP edge only when asked — `build_container(with_api=True)`,
which `./dx serve` passes and the walkthroughs do not. Their `api.py` is worth reading for how
little it takes to put an API in front of something that already works.

## taskflow

```bash
uv run python -m examples.taskflow
```

Read [`taskflow/wiring.py`](./taskflow/wiring.py) first — it is where every dependency is bound
and the file worth copying into a real application. The rest of the package is laid out the way
an application on dexter should be:

| File | Holds |
| --- | --- |
| `domain.py` | Data and abstract contracts — the types used as container keys. Knows nothing about dexter |
| `services.py` | Implementations, and the things that do the work |
| `api.py` | The HTTP edge, added only when `build_container(with_api=True)` asks for it |
| `wiring.py` | Every binding, in one place |
| `display.py` | Output formatting, kept away from the wiring |
| `__main__.py` | The scripted walkthrough |

### What the output shows

Instances are labelled `ClassName#N` in first-seen order, so the same object is recognisable
across lines. Raw `id()` hex would be unreadable and would change every run.

```
3 jobs, handled concurrently, one scope each
  job-a  pool=ConnectionPool#1  repo=InMemoryRepository#2  ctx=RequestContext#3
  job-b  pool=ConnectionPool#1  repo=InMemoryRepository#4  ctx=RequestContext#5
  job-c  pool=ConnectionPool#1  repo=InMemoryRepository#6  ctx=RequestContext#7

within a single scope
  Repository     twice -> InMemoryRepository#8, InMemoryRepository#8  same=True
  RequestContext twice -> RequestContext#9, RequestContext#10  same=False
```

Those two blocks are the whole point:

- `ConnectionPool#1` is identical in all three jobs — **`Scope.SINGLETON`**, one per container
  graph, built once by an async factory.
- The repository differs per job but repeats within a single scope — **`Scope.SCOPED`**, the
  natural lifetime for per-request state such as a unit of work.
- The context differs every single time — **`Scope.TRANSIENT`**.

The walkthrough then covers self-injection (a `Container` parameter receives whichever container
is resolving, so a dependency resolved inside a scope sees that scope), an optional dependency
(`Notifier | None`, injected when bound and `None` when not, with no change to the handler), and
a deliberate resolution failure so you can see that an error names the *path* to the missing
dependency rather than just the dependency.

### Disposal

Two bindings carry a `dispose=`, and the section near the end of the run shows the difference
between them:

```
disposal: releasing what the container created
  inside the scope  UnitOfWork#15 closed=False
                    ConnectionPool#1 open=True
  after the scope   UnitOfWork#15 closed=True
                    ConnectionPool#1 open=True
```

`UnitOfWork` is `Scope.SCOPED`, so the scope that built it releases it on the way out.
`ConnectionPool` is `Scope.SINGLETON` and belongs to the root, so the scope leaves it alone —
it closes when `container.aclose()` runs, which the last line of the transcript shows.

Both callbacks are named explicitly at the binding. dexter never guesses that a method called
`aclose` is the one that releases a type, because that guess is wrong as often as it is right.

### Deliberately not shown

Lazy resolution, because `Lazy[T]` does not exist yet.

### One thing in here that a real program should not copy

`display.tag()` retains a reference to every object it labels. That is deliberate: labels are
keyed on `id()`, CPython reuses an address once an object is collected, and without holding a
reference a just-collected transient would hand its label to whatever is allocated next — making
the transcript claim two unrelated objects are the same one. A demonstration that has to be
trusted cannot afford that. A real program has no reason to keep such a list.

## storefront

```bash
uv run python -m examples.storefront
```

Read [`storefront/wiring.py`](./storefront/wiring.py) first — it is where the CQRS module is
wired and every handler registered, and it is the file worth copying into a real application.

| File | Holds |
| --- | --- |
| `domain.py` | The messages and the abstract contracts. Knows nothing about how anything works |
| `services.py` | Implementations, plus the scoped context that lets a handler correlate what it emits |
| `handlers.py` | One class per message, each with a single async `handle` |
| `middleware.py` | Cross-cutting concerns, applied to all three buses |
| `api.py` | The HTTP edge, added only when `build_container(with_api=True)` asks for it |
| `wiring.py` | `use_cqrs`, then every handler and middleware, in one place |
| `display.py` | Output formatting, kept away from the wiring |
| `__main__.py` | The scripted walkthrough |

### What the output shows

Every dispatch is traced by middleware as `-> Message id` on the way in and `<- Message id` on
the way out. Ids are shortened to a leading block and a tail — the leading block is a UUIDv7
timestamp, identical for everything sent in the same millisecond, so the tail is what tells two
dispatches apart.

```
correlation: the event knows what caused it
  -> PlaceOrder   019fa9b5…a11a
  <- PlaceOrder   019fa9b5…a11a
  -> OrderPlaced  019fa9b5…de06
  -> OrderPlaced  019fa9b5…94d1
  <- OrderPlaced  019fa9b5…de06
  <- OrderPlaced  019fa9b5…94d1
  command      id=019fa9b5…a11a  corr=019fa9b5…a11a
  event        id=019fa9b5…94d1  corr=019fa9b5…a11a  caused_by=019fa9b5…a11a
```

Different message ids, one shared correlation id: the event and the command that caused it are
one causal chain, and the message values themselves carry none of that — it all lives on the
envelope.

The walkthrough then covers a ticket whose id is readable before the work finishes, an event
reaching two reactions concurrently, a query answered inline with no ticket, three commands
dispatched and never redeemed, and two mistakes — resolving a bus outside a scope, and
dispatching a command nothing handles — so you can see what each reports.

The last section binds a third reaction that always fails. The other two still run, and the
failure arrives as a group when the scope is left, rather than as a lone first exception.

### Nobody drains anything

```
leaving a scope settles its buses
  inside the scope   pending=1  ticket done=False
  -> PlaceOrder   019fab9b…81f8
  <- PlaceOrder   019fab9b…81f8
  -> OrderPlaced  019fab9b…d864
  <- OrderPlaced  019fab9b…d864
  after the scope    pending=0  ticket done=True
  reservations added -> 1
```

A command is dispatched and the ticket dropped. Inside the scope nothing has run; by the time
the `async with` has closed, the command has run, the event it published has reached both
reactions, and the ticket is done.

No section here calls `drain()`. `use_cqrs` binds the scope's `BusGroup` with
`dispose=BusGroup.settle`, so leaving a scope waits for every dispatch started in it and
reports whatever failed unredeemed — which is why the last section can catch a `DisposalError`
from the `async with` itself. The three fire-and-forget dispatches in the deferred section
complete for the same reason.

## frontdesk

```bash
uv run python -m examples.frontdesk
```

A hotel front desk: an HTTP API over a CQRS core, wired by the container. Read
[`frontdesk/wiring.py`](./frontdesk/wiring.py) first — it is the only file that mentions all
three modules, and it shows how little joins them.

| File | Holds |
| --- | --- |
| `domain.py` | The CQRS messages, the API request and response models, and the abstract contracts |
| `services.py` | The book, the audit trail, and `current_tenant` — the factory worth reading twice |
| `handlers.py` | API handlers on one side, command/query/event handlers on the other |
| `middleware.py` | `RequireTenant`, which refuses a request, and `Trace`, which wraps every one |
| `wiring.py` | Every binding, in one place |
| `asgi.py` | Calls the application directly, so the walkthrough needs no server |
| `display.py` | Output formatting, kept away from the wiring |
| `__main__.py` | The scripted walkthrough |

**No server is started and no port is bound.** `create_app` returns an ASGI application, and
`asgi.py` invokes it with the three dictionaries a server would. That is the whole reason the
library hands back an application instead of running one.

### What the output shows

Each request prints as it is made, then the status and body that came back. The `->` and `<-`
lines are the `Trace` middleware wrapping every request.

```
one request model, filled from wherever the path says
  GET /rooms  ?floor=2&limit=2
  -> SearchRoomsApi
  <- SearchRoomsApi
     200  ["201", "202"]
  POST /bookings  {"room": "101", "nights": 3}
  -> BookRoomApi
  <- BookRoomApi
     201  {"reference": "BK-001", "room": "101"}  location: /bookings/BK-001
```

Two sections are worth reading closely.

`identity` books rooms as two different tenants and then prints the audit trail. `Audit` never
reads a header — it declares `tenant: Tenant`, and `current_tenant` builds one per request from
the `RequestContext`. That is the pattern to copy when something deep in your graph needs to
know who is calling; the alternative, a process-wide global, hands one request's caller to
another under any concurrency at all.

`cqrs` books a room and reads the housekeeping list on the very next line:

```
  POST /bookings  {"room": "301", "nights": 2}
     201  {"reference": "BK-004", "room": "301"}
  housekeeping, read straight after -> ['301']
```

The API handler dispatched a command and awaited only its result. The event that command's
handler published was still in flight when it returned — but leaving the request scope settled
the buses, so the reaction had run before the response was built. `dexter.api` does not import
`dexter.cqrs` to arrange that: `use_cqrs` binds a `dispose=`, and the container does the rest.
