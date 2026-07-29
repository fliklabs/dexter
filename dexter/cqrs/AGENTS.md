# dexter/cqrs/AGENTS.md

Commands, queries, events and their buses. Read the source directly; this file records only
the decisions that are not visible in it.

## Layout

| File | Holds |
| --- | --- |
| `models.py` | The three message bases, `Envelope`, the handler and middleware protocols |
| `errors.py` | The exception tree. Imports from nothing in this module |
| `_introspection.py` | Private. Reads a message's declared result and a handler's shape |
| `registry.py` | The three registries — which handler handles which message |
| `dispatch.py` | `Dispatch` / `EventDispatch` — the ticket |
| `pipeline.py` | `MiddlewarePipeline` — ordered composition around a dispatch |
| `bus.py` | `MessageBus` — outstanding work, draining, closing — and `BusGroup` |
| `command_bus.py`, `query_bus.py`, `event_bus.py` | One abstract key and its in-process implementation each |
| `use.py` | `use_cqrs` and the `register_*` functions |

Promote `models.py` to `models/{message,envelope,handler,middleware}.py` when it crosses ~300
lines, and `command_bus.py` to `command_bus/` when a second transport lands. Both keep
`__init__.py` re-exporting the same names, so importers never change.

## `dispatch` and `publish` are `def`, not `async def`

A ticket you have to await before you can read its id is not a ticket. The whole point is that
the id exists the moment the bus accepts the message, so it can be logged, correlated, or
returned to a caller while the work is still running.

This does not breach the async-native rule. Nothing drives a loop and nothing wraps async as
sync; both methods need a running loop and are unusable without one. What *is* deliberate is
which checks stayed synchronous: the registry lookup is a dict hit, so **an unhandled message
raises at the call site**, before any task exists. Only constructing the handler and running it
are deferred, because both are genuinely asynchronous.

`ask` is the exception, and is `async def` returning the result directly. A read has no side
effect worth deferring and no identity worth correlating; a ticket would make every call site
write `await (await ask(q)).result()` for nothing.

## Every bus must be `Scope.SCOPED`

A bus takes the `Container` and resolves handlers from it at dispatch time, so its lifetime
decides which container those handlers come from. A singleton bus would receive the **root** —
`ContainerBuilder` gives a singleton's `Container` parameter the root, never the asking scope —
and would then resolve every handler there, bypassing the scope it was asked for. Resolving a
bus from the root therefore raises `ScopeRequiredError`, which is correct.

## The command/result agreement is enforced twice

**A dependency of the design, not an oversight.** `register_command_handler` pins the message
type in both its arguments, so mypy rejects a handler for a different command. It cannot pin
the *result*: that needs `TCommand: Command[TResult]`, a type parameter bounded by another type
parameter, which mypy does not support — and when two arguments constrain one variable mypy
joins them instead of reporting the conflict. Verified, both ways.

So `_introspection.py` checks the result half at registration time, reading the declared type
from `__pydantic_generic_metadata__` (walking the MRO, because pydantic leaves `__orig_bases__`
pointing at `BaseModel` and `Generic[TResult]`, which is useless for a subclass of a concrete
command). `tests/cqrs/test_typing.py` asserts the static half and `test_registration.py` the
runtime half. Deleting either loses a guarantee silently.

A handler returning a **subclass** of the declared result is accepted: returning something more
specific than promised is always safe.

## Registry first, container second

`register_*` records in the registry *before* binding in the container. Reversed, a malformed
or duplicated handler is reported by whichever of the container's own guards trips first —
`InvalidRegistrationError: 'X' cannot be used as a key` instead of `InvalidHandlerError:
X.handle is not asynchronous`. The precise error is the one worth having.

The consequence is that a failed container binding leaves the registry populated. The builder
is already unusable at that point, so this is not worth guarding.

## Lookup is on the exact runtime class

No walking base classes, no matching by name, no import-time scanning. Inheritance-based
lookup makes "which handler ran?" depend on MRO order, and a registration you cannot find by
reading the wiring is one nobody can reason about. A subclass of a registered command needs its
own registration.

## Failures are reported once, to whoever asked

`Dispatch.result()` marks the ticket observed. `drain()` raises only failures that were never
redeemed. Without that, the ordinary pattern — `try: await ticket.result() except ...` — would
produce a *second* exception at scope exit for a failure already handled.

A ticket is held until `drain()` or `aclose()`, never released when its task finishes: a
failure that completed unobserved is precisely what `drain()` exists to report. A bus is
scoped, so what it holds is bounded by one scope.

## The buses settle together, and the container does it

`use_cqrs` binds `BusGroup` with `dispose=BusGroup.settle`, so leaving a scope waits for every
dispatch started in it. This is not tidiness: a dispatch resolves its handler *from the scope*,
so a task that had not got that far yet finds the scope closed underneath it. The failure
depends on whether the task was scheduled before the scope exited, which means it passes tests
and fails under load.

**Why a group rather than a `dispose=` on each bus.** Reverse creation order is right for
releasing a resource, because releasing only ever removes work. Draining *creates* work on the
other buses. A command handler publishing an event is the central CQRS pattern, and the event
bus is constructed inside that handler — so it finishes construction after the command bus, and
reverse order drains it first, while it is still empty. Draining the command bus next then
publishes into a bus that has already been settled, and the reaction escapes the scope. That
bug was real and is pinned by
`tests/cqrs/test_settling.py::test_waits_for_an_event_published_by_a_command_handler`.

So the group drains every bus in rounds until all of them are quiet, then closes them. Each bus
takes the group and adds itself, which is what guarantees the group exists whenever any bus
does. `drain()` loops for the same reason, one bus deep: a handler may dispatch again.

**Leaving a scope therefore blocks until its dispatches finish**, exactly like
`asyncio.TaskGroup`. A handler that never completes hangs scope exit — which is the honest
outcome, since the alternative is silently discarding the work.

## Middleware wraps a dispatch, not a handler

One dispatch is one pass through the pipeline, including a publish that fans out to five
handlers. A transaction or an authorisation check belongs per dispatch; a concern that really
is per handler belongs in the handler.

There is one shared pipeline for all three buses, so a concern is registered once. Middleware
that only cares about one kind of message can look at `envelope.message`.

## A handler sees the message, not the envelope

So a handler cannot, on its own, mark an event it publishes as caused by the message it is
handling. Closing that gap needs a scoped context object that middleware fills in, which is an
application concern rather than something to build in — `examples/storefront` demonstrates it in
about fifteen lines. Passing the envelope to `handle` instead was rejected: it would put dexter
in every handler signature and every handler test.

## Not implemented yet

Out-of-process transports. The abstract bus keys exist so one can be bound without touching a
call site, and `Envelope` already carries the identity such a transport needs, but nothing
serialises a message and there is no dispatcher for inbound ones.
