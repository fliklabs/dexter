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

## Install

```bash
uv add "dexter @ git+https://github.com/fliklabs/dexter"
```

Runtime dependencies: `pydantic>=2.12` (which brings `pydantic-core`,
`annotated-types`, `typing-extensions` and `typing-inspection`). The floor is hard —
earlier pydantic releases have no cp314 wheel and fail to build on Python 3.14.

Note that importing a framework module costs roughly 45 ms, because pydantic's schema
machinery loads when the first model is defined. `import dexter` on its own stays free.

## Dependency injection

Wire an application with a `ContainerBuilder`, then resolve from the `Container` it
builds:

```python
from dexter.dependency_injection import ContainerBuilder, Scope

builder = ContainerBuilder()
builder.register(Repository).to(SqlRepository, scope=Scope.Scoped)
builder.register(Pool).to(open_pool, scope=Scope.Singleton)  # async factory
builder.register(Clock).to_instance(SystemClock())

container = builder.build()

async with container.scope() as scope:
    handler = await scope.resolve(Handler)
```

Bindings are two calls so that a type checker can verify them — see below. `scope` is
required: lifetime is too consequential to be chosen by omission.

| Scope | Lifetime |
| --- | --- |
| `Scope.Transient` | A new instance for every resolution |
| `Scope.Singleton` | One instance permeating every scope |
| `Scope.Scoped` | One instance spanning every resolution within one scope |

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
builder.register(Repository).to(SqlRepository, scope=Scope.Scoped)

# Inferred as Repository, with no suppression at the call site.
repository = await container.resolve(Repository)
```

Binding is deliberately two calls because that is what makes the provider checkable.
mypy will reject a provider that cannot produce the key:

```python
builder.register(Repository).to(returns_an_int, scope=Scope.Transient)
# error: Argument 1 to "to" of "Binder" has incompatible type "Callable[[], int]";
#        expected "type[Repository] | Callable[..., Repository]
#                  | Callable[..., Awaitable[Repository]]"  [arg-type]
```

Collapsing that into one call would mean widening the key so abstract types are
accepted, and that widening makes mypy infer the type variable as `object` — silently
accepting the wrong provider. Two calls buys the check.

## Development

Requires [uv](https://docs.astral.sh/uv/). Python is installed and pinned by uv —
you do not need a system Python.

```bash
uv sync                                    # create .venv and install dev dependencies
./verify.sh --fix                          # format, lint, type-check, test
./verify.sh                                # same, without writing changes
uv run pytest tests/dependency_injection   # run one module's tests
```

A change is not finished until `./verify.sh` exits 0.

See [AGENTS.md](./AGENTS.md) for repository structure and conventions.

## Licence

MIT — see [LICENSE](./LICENSE).
