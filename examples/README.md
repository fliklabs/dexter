# Reference applications

Runnable programs that demonstrate dexter in use. They exist because some of the library's
behaviour cannot be seen by reading signatures — you cannot tell from a type that a singleton is
shared across scopes while a scoped instance is rebuilt per scope.

These are **demonstrations, not tests**. They assert nothing. They are type-checked under mypy
strict and linted like the rest of the repository, so they cannot silently drift out of step
with the API, but nothing in the test suite executes them.

They are not shipped in the wheel, so nothing here affects what a consumer installs.

| Example | Shows |
| --- | --- |
| [`taskflow/`](./taskflow) | A simulated async job service: all three scopes, async factories, optional dependencies, self-injection, and what a resolution failure reports |

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

- `ConnectionPool#1` is identical in all three jobs — **`Scope.Singleton`**, one per container
  graph, built once by an async factory.
- The repository differs per job but repeats within a single scope — **`Scope.Scoped`**, the
  natural lifetime for per-request state such as a unit of work.
- The context differs every single time — **`Scope.Transient`**.

The walkthrough then covers self-injection (a `Container` parameter receives whichever container
is resolving, so a dependency resolved inside a scope sees that scope), an optional dependency
(`Notifier | None`, injected when bound and `None` when not, with no change to the handler), and
a deliberate resolution failure so you can see that an error names the *path* to the missing
dependency rather than just the dependency.

### Deliberately not shown

Disposal and lazy resolution, because dexter does not have them yet. `container.aclose()` is
called at the end and releases the container's own state, but it does not yet call anything on
resolved instances — so the pool in this example is never closed. That lands in a later change,
along with `Lazy[T]`.

### One thing in here that a real program should not copy

`display.tag()` retains a reference to every object it labels. That is deliberate: labels are
keyed on `id()`, CPython reuses an address once an object is collected, and without holding a
reference a just-collected transient would hand its label to whatever is allocated next — making
the transcript claim two unrelated objects are the same one. A demonstration that has to be
trusted cannot afford that. A real program has no reason to keep such a list.
