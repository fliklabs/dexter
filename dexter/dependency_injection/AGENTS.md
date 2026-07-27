# dexter/dependency_injection/AGENTS.md

Async-native dependency injection. Read the source directly; this file records only the
decisions that are not visible in it.

## Layout

| File | Holds |
| --- | --- |
| `models.py` | `Scope`, the `Key`/`Provider` aliases, `Registration`, and the resolution-path types |
| `errors.py` | The exception tree. Imports from `models.py`, never the reverse |
| `_annotations.py` | Private. Turns a provider into a `DependencyPlan` |
| `container_builder.py` | `ContainerBuilder` and `Binder` — registration only |
| `container.py` | `Container` — resolution, scopes, the in-flight task map |

`models.py` is near the ~300-line promotion trigger. When it crosses, promote to
`models/{scope,registration,resolution_chain,dependency_plan}.py` with `models/__init__.py`
re-exporting the same names, so importers do not change. `ResolutionChain` moves first — it is
the one with real behaviour.

There is no `use.py`. This module is what *other* modules register into, so it has nothing to
register until `dexter.application` exists.

## Why registration is two calls

`builder.register(Key).to(Provider, scope=...)` is not stylistic. A single call would have to
widen the key to `type[T] | Callable[..., T]` so abstract classes and protocols can be keys
without consumers suppressing mypy's `type-abstract`. That widening makes mypy solve `T` as
`object`, and a provider producing the wrong type is then **silently accepted**. Splitting the
call pins `T` on `register` and checks the provider against `Binder[T]`.

`tests/dependency_injection/test_typing.py` runs mypy over deliberately-wrong bindings and
asserts it fails. If that test is ever deleted, the guarantee this design exists for goes with
it, silently.

## The lifetime rule

**A dependency must live at least as long as whatever depends on it.** That reduces to one
prohibition: **a `Singleton` may not depend, transitively, on a `Scoped` key.** A singleton
outlives every scope, so whichever scope's instance it captured first would be shared with all the
others — the classic captive dependency. `Scoped → Singleton`, `Transient → anything` and
`Singleton → Transient` are all fine.

Enforced in two places, because one half cannot be known statically:

| Where | Catches | Error |
| --- | --- | --- |
| `ContainerBuilder.build()` | Declared `Singleton → … → Scoped` edges, walking transitively | `CaptiveDependencyError` |
| `Container._resolve` | A `Scoped` key resolved from the root, which is not a scope | `ScopeRequiredError` |

The runtime half is needed because a service holding a `Container` can resolve anything from
wherever it was handed, which no static check can see. The transitive walk stops at a `Container`
parameter: that is a lazy boundary, resolved later against whichever container is asking, so
nothing is captured. (`Lazy[T]` will stop it too, once it exists.)

**A `Singleton` provider's `Container` parameter receives the root**, not the scope that asked.
That is correct — a singleton must not hold a scope — and it is why anything that resolves
per-request work from an injected container must itself be `Scoped`. The reference app's
`JobDispatcher` is the worked example.

## Closing is terminal, for the whole ancestry

`_ensure_open` walks to the root and refuses if **any** ancestor is closed. Checking only
`self._closed` is not enough: a scope keeps its own cache but resolves singletons through the
root, so a live child of a closed parent would keep working — and would rebuild a singleton on the
closed root and cache it there, quietly reviving a container that was finished with. Because
`scope()` runs the same check, a grandchild cannot be created beneath a closed ancestor either.

A parent does **not** dispose or clear its children. There is nothing to dispose yet; recursive
teardown belongs with the disposal change.

## Scope is required

`Binder.to` has no default scope. Lifetime is the most consequential property of a binding, and
in the container this replaces, 148 of 246 registrations silently took a default that meant "new
instance every time" — almost certainly not all deliberately.

## Concurrency

A `Container` belongs to one event loop. It holds **no locks**. Mutual exclusion during cached
construction comes from a per-key in-flight `asyncio.Task`; the check-then-insert has no `await`
between its two halves, so on one loop the map itself is the mutual exclusion.

Three properties, each with a test:

- Concurrent resolutions of one key produce exactly one instance.
- A resolver awaits through `asyncio.shield`, so cancelling one resolver does not cancel
  construction for the others. **A `dict[key, asyncio.Future]` fails this** — cancelling the
  creating resolver propagates to innocent waiters. That is why it is a `Task`.
- A failure reaches every waiter and evicts the entry, so a later resolve retries rather than
  replaying a cached failure.

**A single `asyncio.Lock` around resolution deadlocks**, because resolution re-enters itself and
`asyncio.Lock` is not reentrant.

Do not route every resolution through a task: `create_task` plus `await` costs roughly 500× a
direct `await`. Cache hits return from a plain dict and `Scope.TRANSIENT` is built inline; only
the first miss on a cached scope creates a task. `test_concurrency.py` asserts a cached resolve
never suspends.

## Cycle detection order matters

The cycle and depth checks live in `Container._resolve`, **before** the in-flight map is
consulted. Moving them into the construction path reintroduces a deadlock: for a cached scope,
the second request for a key already under construction would await its in-flight task, which is
itself blocked on the other half of the cycle. It hangs instead of raising.

A `Container`-annotated parameter is satisfied from the resolver itself, so it is not a graph
edge and cannot close a cycle.

## Not implemented yet

Disposal (`aclose`, `dispose=`, reverse-order release, `BaseExceptionGroup` aggregation) and
`Lazy[T]` are the next change. `Container` is already an async context manager so that change
adds nothing breaking. Until then `aclose()` releases the container's own state and cancels
in-flight construction, but calls nothing on resolved instances.

`lock_scope`-style pinning of an instance to an ancestor scope is deliberately absent. In the
container being replaced it existed at two call sites to paper over scopes having only one
level; keyed or named scopes are the right fix if the need reappears.
