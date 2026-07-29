# AGENTS.md

`dexter` is a collection of independent, reusable framework modules. Each module under
`dexter/` stands alone and is imported directly:

```python
from dexter.dependency_injection import Container
```

`dexter/__init__.py` re-exports nothing, so `import dexter` never pulls in every
framework. Read source files directly — there is no external documentation.

## Modules

| Module | Status | Purpose |
| --- | --- | --- |
| `dexter.commons` | Scaffolded | Shared primitives; root of the exception hierarchy |
| `dexter.dependency_injection` | Implemented | Async DI container, scopes, resolution |
| `dexter.cqrs` | Implemented | Commands, queries, events and their buses |
| `dexter.cli` | Implemented | A keyboard-navigable CLI. Ships no commands — consumers register their own |
| `dexter.api` | Implemented | Typed request handlers, exposed over HTTP. `dexter.api.http` is the only part that knows a web framework exists |
| `dexter.application` | Implemented | Composing an application from modules. The only module that depends on two others, which is its job |
| `dexter.caching` | Planned | Cache abstractions |
| `dexter.observability` | Planned | Tracing and instrumentation |

Planned modules do not exist as packages. Do not create an empty package to reserve a
name — an importable package with nothing in it is worse than no package.

## Structure: file vs folder

**A concept gets a file. When one file is no longer enough, it becomes a folder of the
same name.**

| Use a | When |
| --- | --- |
| File (`container.py`) | One cohesive concept, one public export or a tight family, under ~300 lines |
| Folder (`container/`) | The concept outgrew a file, or it is a namespace holding several siblings of the same kind |

Promote only when one of these is true:

- The file exceeds ~300 lines.
- The concept has three or more distinct variants or implementations.
- It has acquired sub-concepts that deserve their own names.

**Promotion must not break importers.** `x.py` becomes `x/__init__.py` plus
`x/<part>.py`, where `x/__init__.py` re-exports exactly the names `x.py` exported. Callers
never change, so growth never forces a refactor downstream.

**Every `__init__.py` contains re-exports only** — never logic, never definitions. The
public surface of a directory is then readable at a glance, and no `__init__.py` needs a
test.

**Maximum depth is three levels below `dexter/`** (`dexter/<module>/<group>/<file>.py`).
Deeper means the module should be split into two modules.

## Module anatomy

Every module uses the same well-known filenames so navigation is uniform:

| Path | Required | Purpose |
| --- | --- | --- |
| `__init__.py` | Always | Public API — re-exports only |
| `errors.py` | Always | The module's exception tree, rooted in `DexterError` |
| `models.py` → `models/` | When it has data types | Value objects, enums, protocols — shape, not behaviour |
| `<concept>.py` | As needed | Behaviour, one concept per file |
| `use.py` | Once wiring exists | The module's registration entry point |
| `AGENTS.md` | When non-obvious | Module-scoped guidance |

`py.typed` lives **only** at `dexter/py.typed`. PEP 561 applies recursively, so
per-module markers are wrong.

## Wiring: what `use.py` contains

A module that registers anything into a container exposes exactly two shapes, and the
difference between them is the point. `dexter/cqrs/use.py` is the worked example.

| Shape | For | Rules |
| --- | --- | --- |
| `use_<module>(builder) -> None` | What the **module** provides | No configuration arguments — it is a topology switch, not a settings object. An alternative topology is a second `use_*` function, never a flag |
| `register_<thing>(builder, ..., *, scope) -> None` | What the **application** contributes | Called many times. `scope=` is required, for the same reason `Binder.to` requires it |

- **`use_*` runs before every `register_*`.** The registries a `register_*` writes into are
  created by `use_*`. The wrong order must raise a module error naming the missing call, not
  the container's own "not registered as an instance".
- **Neither returns the builder.** `ContainerBuilder` is not a chaining API — `register`
  returns a `Binder` and `build` returns a `Container` — so returning it would invent a second
  style.
- **Register into the module's own registry before binding in the container**, so a malformed
  registration is reported by the module's precise error rather than by whichever container
  guard trips first.
- Anything a `register_*` must populate while wiring is bound with `to_instance` and fetched
  back with `ContainerBuilder.resolve_instance`, which exists for exactly this.

## Naming

- `snake_case` for modules and packages. Singular for one concept (`container.py`),
  plural for a collection of like things (`errors.py`, `models.py`).
- One public class per file where practical, file named after it: `container_builder.py`
  defines `ContainerBuilder`.
- **No `utils.py` grab-bags.** Name utilities after what they operate on:
  `type_utils.py`, `string_utils.py`.
- Leading underscore (`_internal.py`) for module-private helpers that are not re-exported.

## Enums

**Every enum is a `StrEnum` with `UPPER_CASE` members whose value is written out and equals the
member name.**

```python
class Scope(StrEnum):
    TRANSIENT = "TRANSIENT"
    SINGLETON = "SINGLETON"
    SCOPED = "SCOPED"
```

`UPPER_CASE` is what CPython's enum HOWTO recommends, under the heading "Case of Enum Members":
*"we strongly recommend using UPPER_CASE names for members"*. Its second stated reason is
functional, not aesthetic — it avoids collisions between member names and the mixin type's
attributes, which for a `StrEnum` means every lowercase `str` method (`title`, `strip`, `format`,
`index`, `count`, `split`). Nothing in PEP 8 addresses enums at all, and no ruff rule enforces
any casing here, so this is convention: follow it anyway.

**Never `auto()`.** In a `StrEnum` it produces the *lower-cased* member name, which is documented
behaviour: *"Using `auto` with `StrEnum` results in the lower-cased member name as the value."*
The consequence is worse than it looks — name lookup and value lookup then accept disjoint
strings:

| | `Scope["SINGLETON"]` | `Scope("SINGLETON")` | `Scope("singleton")` |
| --- | --- | --- | --- |
| `SINGLETON = auto()` | ok | **ValueError** | ok |
| `SINGLETON = "SINGLETON"` | ok | ok | ValueError |

The HOWTO also describes `auto()` as signalling that *"these values are not important"*, which is
false for anything that reaches a config file, a log line, or an error message. Write the value.

**Value must equal the name.** One canonical spelling then works everywhere — Python, JSON, tests,
error messages — and `Scope(x) is Scope[x]` holds. There is direct stdlib precedent in a
wire-facing `StrEnum`: `http.HTTPMethod` is `GET = "GET"`.

**In developer-facing messages, render the symbol, not the value.** `StrEnum.__str__` returns the
bare value, so an interpolated member shouts. Use a small `describe_scope`-style helper that
produces `Scope.SINGLETON` — the text the reader has to type in their own wiring.

## Data types: pydantic or slotted class

Pydantic is a first-class citizen and replaces `@dataclass`, but not everywhere:

| Use | For |
| --- | --- |
| `pydantic.BaseModel`, `frozen=True`, `extra="forbid"` | Types that cross from user input into dexter and are built once, where validation earns its cost |
| Plain class with `__slots__`, or a tuple | Types dexter builds for itself on a hot path |

The split is measured, not stylistic: a pydantic model costs ~298 ns to construct versus ~38 ns
for a slotted class, and using one per resolution step made dependency resolution 63% slower.
Keep every field of a frozen model hashable and immutable — a `list` field silently makes the
model unhashable, and frozen is shallow.

## `dexter.commons`

Two constraints beyond the normal rules:

1. **It must never import from another dexter module.** It is the bottom of the
   dependency graph; a cycle here would be unfixable.
2. **Nothing enters until at least two modules need it.** Speculative shared code is how
   a commons package rots. A one-consumer helper stays in the module that uses it until a
   second consumer appears.

Rule 2 is what put `type_utils.py` there: `describe_type` renders a class as the name a reader
has to find in their own wiring, and it stayed inside the dependency injection module until
`dexter.cqrs` needed the same rendering. Nothing else is added in advance.

## Public API must be re-exported explicitly

mypy `strict` enables `no_implicit_reexport`, so a plain `from .container import Container`
in `__init__.py` does **not** re-export the name: consumers' imports would resolve to
`Any` or fail outright.

**Use the redundant-alias form:**

```python
from .container import Container as Container
```

This needs no `__all__` — ruff's `PLC0414` does not fire in `__init__.py`, and the
redundant alias is ruff's own preferred fix for first-party `__init__.py` imports. Every
re-exported name is covered by `tests/test_public_api.py`.

## Annotations

**Read annotations with `typing.get_type_hints(target, format=Format.FORWARDREF)`**, never raw
`__annotations__` and never `inspect.signature(...).parameters[...].annotation`. `Format` comes
from **`annotationlib`**, not `typing`.

Raw annotations are plain strings whenever the defining module uses
`from __future__ import annotations`, which silently breaks runtime type introspection.
`get_type_hints` resolves them to real objects either way, so dexter imposes no restriction on
how consumers write their own annotations. Prefer it over `inspect.get_annotations`, which
returns a bare string rather than a `ForwardRef` for an unresolvable name and un-stringises only
one level deep.

`Format.FORWARDREF` means an unresolvable annotation arrives as a `ForwardRef` you can turn into
a precise error, instead of a `NameError` escaping from introspection.

Traps when introspecting constructors, every one of them verified:

- **`inspect.signature` needs `annotation_format=Format.FORWARDREF` too.** By default it
  evaluates annotations eagerly and raises `NameError`, which defeats the point of reading
  hints in forward-reference mode.
- The dict from `get_type_hints` is **not** in signature order. Iterate
  `inspect.signature(...).parameters` and look hints up **by name**; never zip the two.
- **Do not test for a constructor with `"__init__" in vars(cls)`.** A subclass that inherits a
  constructor with real dependencies reports `False`, so every inherited dependency is silently
  dropped. Select the construction target like this:

  ```python
  if getattr(cls, "_is_protocol", False):        # a Protocol is not constructible
  elif cls.__init__ is not object.__init__:      # introspect __init__
  elif cls.__new__ is not object.__new__:        # NamedTuple and friends construct here
  else:                                          # no dependencies
  ```

- `inspect.signature` raises `ValueError` for some builtins — catch it.
- `get_type_hints` on a `functools.partial` silently returns `{}`. Take parameters from the
  partial and hints from the unwrapped `.func`.
- To detect an async provider, prefer awaiting the *result* when
  `inspect.isawaitable(result)`: `inspect.iscoroutinefunction` misses a class with an async
  `__call__` and a sync function returning an awaitable.
- **`asyncio.iscoroutinefunction` is banned** — deprecated in 3.14, removed in 3.16, and its
  `DeprecationWarning` is fatal under this project's warning filter. Use
  `inspect.iscoroutinefunction`.

## Tooling

`uv` for dependencies, `ruff` for formatting and linting, `mypy --strict` for types,
`pytest` with `pytest-asyncio` in auto mode for tests. Configuration lives entirely in
`pyproject.toml`.

**Runtime dependencies: `pydantic>=2.12`, `click>=8.3`, `rich>=14`, `fastapi>=0.115`.** Every
one was a deliberate decision, because consumers inherit everything declared here — and a
module they never import still lands in their install. Adding a fifth is the same decision
again, not a convenience.

| Dependency | Why | Floor is hard because |
| --- | --- | --- |
| `pydantic` | Data types that cross into dexter | Earlier releases have no cp314 `pydantic-core` wheel and fail to build on 3.14 |
| `click` | `dexter.cli`'s command tree, parsing and help | Nothing older is typed well enough for strict mode |
| `rich` | `dexter.cli`'s output | — |
| `fastapi` | `dexter.api`'s routing, validation and schema generation | Pydantic models as query parameters arrived in 0.115.0, and a route whose path names no parameter binds its whole request model that way |

`fastapi` is the one that costs a consumer something they may not want: it brings `starlette`,
`anyio`, `sniffio` and three smaller packages into an install that only wanted the container.
That is why `dexter/api/__init__.py` imports none of it and `dexter.api.http` is a package of
its own — the cost is real, and the boundary is where it is paid.

`curses` costs nothing: it is stdlib. It is also POSIX-only, which is why `dexter.cli` imports
it lazily and works non-interactively everywhere.

**dexter is async-native.** No synchronous entry points, no async↔sync wrappers, and nothing in
the library drives an event loop (`asyncio.run`, `run_until_complete`) on a caller's behalf.

```bash
uv sync                       # create .venv, install dev dependencies
./verify.sh --fix             # format, lint, type-check, test
./verify.sh                   # same, without writing changes
uv run pytest tests/commons   # one module's tests
./upgrade.sh                  # move every dependency forward, keep it only if the gate passes
```

**A change is not done until `./verify.sh` exits 0.**

### Upgrading dependencies

`./upgrade.sh` resolves the newest version of everything, raises each declared floor to match,
runs the gate, and keeps the result **only if it passes**. On any failure — including Ctrl+C —
`pyproject.toml` and `uv.lock` are restored and the environment is re-synced, so a failed
upgrade leaves nothing behind. `--lock-only` moves the lock without touching the floors,
`--dry-run` shows what would change and puts it back.

Three decisions in it are worth knowing:

- **Floors come from `uv.lock`, never from an index.** uv has already resolved a set satisfying
  `requires-python` and everything else; reusing its answer means a floor can only be a version
  that demonstrably resolves. Asking PyPI separately invites writing one that nothing installs.
- **It runs the whole gate, not just pytest.** `ruff` and `mypy` are themselves upgraded, and a
  new release of either fails files no test would notice.
- **Floors stay `>=`.** dexter is a library; one that pins exactly is one a consumer cannot
  install beside anything that disagrees. The exact set is what `uv.lock` is for.

It does not touch `.python-version` — that pin is the separate decision described above.

**The floor table below is prose, and an upgrade does not rewrite it.** After a runtime floor
moves, read it: the "why" column names specific versions, and those reasons do not
automatically survive the number changing.

### The Python version is written once

Two files mention a Python version. They are not duplicates — they answer different
questions, and are allowed to differ:

| File | Means | Hand-edited? |
| --- | --- | --- |
| `pyproject.toml` → `requires-python` | The public compatibility floor: the oldest Python a consumer may install on | **Yes — the only one** |
| `.python-version` | The interpreter used for local development and CI | No — `uv python pin` generates it |

Nothing else repeats the number: ruff infers `target-version` from `requires-python`,
and mypy follows the interpreter `uv run` selects. `tests/test_python_version.py`
enforces that, plus the rule that the pin is never *below* the floor.

`.python-version` is optional — uv resolves an interpreter from `requires-python`
alone. It is committed so that a fresh clone does not silently drift onto a newer
Python as new releases land. Delete it and uv will pick the newest stable version
satisfying the floor (it ignores pre-releases).

Two invariants keep that working — breaking either silently changes the version being
checked, with no error:

1. **Never invoke `ruff --config <file>`.** Passing a config file explicitly disables
   version inference, and ruff falls back to a default.
2. **Always run mypy as `uv run mypy`.** A global mypy, `uvx mypy`, or a pre-commit hook
   with its own isolated environment would each check against a different Python version.

To change Python version: edit `requires-python`, then
`uv python pin <version> && uv lock`.

### Two ruff rule families are permanently disabled

Do not enable these. Both reintroduce the exact failure the annotation rule above exists
to prevent:

| Family | Why |
| --- | --- |
| `FA` | `FA102` auto-adds `from __future__ import annotations`, stringifying every annotation |
| `TC` | Moves imports into `if TYPE_CHECKING:`; classes named in annotations that are resolved at runtime must stay importable at runtime |

`ANN` is also omitted, as redundant with mypy strict's `disallow_untyped_defs`.

## Repository tooling

`tools/` at the repo root holds this repository's own CLI, built on `dexter.cli`. It is outside
`dexter/`, so the wheel whitelist keeps it out of the distribution exactly as it does `tests/`
and `examples/`. Its entry point is `./dx`, a launcher in the same shape as `./verify.sh`.

`./dx` and `./verify.sh` are not rivals. `verify.sh` is the gate, is what CI runs, and is
unchanged by any of this; `./dx test` is the feedback loop, and reports pass rate, timing and
coverage that the gate does not. `./dx verify` just shells out to the gate.

**`./dx refapp web` is the only thing in this repository that binds a port.** It serves the
reference application so it can be poked from a browser, and it lives in `tools/` rather than
`examples/` for one reason: CI smoke-runs the example with no timeout, and a server would hang
it. The example itself still terminates — `./dx refapp worker`, or
`python -m examples.storefront`,
runs the same container as a worker. Two commands because there are two application kinds, not
because there are two services. Its `uvicorn` dependency
is in the `tools` group, never in `[project.dependencies]` — `dexter.api` hands back an ASGI
application and never runs one, so a consumer inherits no server from us.

**`tools/**/*.py` ignores `T20` only.** Writing to a terminal is the whole job.

## Examples

**There is one reference application**, `examples/storefront`, and it is the thing a consumer
copies to start a service. It is composed from modules under `modules/`, each with its own
`use_<module>` — see `dexter/application/AGENTS.md`. Adding a second example needs a reason:
one app that is obviously the one to copy beats three that each teach a different third of the
framework.

Runnable reference applications live in `examples/` at the repo root — never inside `dexter/`,
and never in the wheel (`packages = ["dexter"]` is a whitelist, so leakage is impossible). They
are included in the sdist so a source download is self-documenting.

**An example is a demonstration, not a test.** It asserts nothing and no test executes it;
shaping it around a test runner would distort it into something nobody would copy. It is
type-checked under **full** mypy strict and linted like everything else, which is its only
protection against drifting out of step with the API — and evidence that a consumer's code can
type-check too.

- Every example directory needs an `__init__.py`, for the same mypy module-naming reason as
  `tests/`.
- Entry point is `python -m examples.<name>`. **Never a `[project.scripts]` console script** —
  it would land in the wheel's `entry_points.txt` and install a command pointing at a module the
  wheel does not contain.
- `examples/**/*.py` ignores `T20` only: printing is the whole point. Docstring rules stay on,
  because an example is documentation.
- CI smoke-runs the app after the distribution check. That is not a test — it asserts nothing;
  it catches the one kind of rot type checking cannot.

## Tests

Tests live in a root `tests/` tree mirroring the package (`tests/<module>/`), not inside
`dexter/`. Co-locating would give every module's test package the same module name, which
mypy rejects as a duplicate; keeping them outside also means the wheel whitelist
(`packages = ["dexter"]`) makes leakage into the distribution impossible.

**Module-local test support goes in `tests/<module>/conftest.py`**, which pytest scopes to
that directory subtree. That is what keeps each module's fixtures self-contained. Adding a
module is `mkdir tests/<module>/` — no central file to update.

Every test directory needs an `__init__.py` so mypy can derive unique module names.

Conventions:

- Module docstring saying what is covered.
- Group by scenario: `class TestHappyPath:`, `class TestGuard:`.
- Name tests as sentences: `test_raises_when_dependency_is_unregistered`.
- Local `make_<entity>()` factories at the top of the file; promote to `conftest.py` only
  once genuinely shared.
- Assert observable behaviour — raised exception, call arguments, return value — not
  internal state.

Tests are type-checked under full strict along with the package. For a test that
deliberately passes a wrong type, bind the value to an `Any`-typed local rather than using
`# type: ignore`, which `warn_unused_ignores` would then flag.

**Must have tests:** anything that rejects input or state, error paths, non-trivial
branching. **No tests needed:** re-export lines, trivial data holders.

### Coverage: 90%, enforced

`verify.sh` measures coverage and fails below the floor in `pyproject.toml`, so CI fails below
it. It is a gate, not a dashboard.

90 is chosen to be high enough to mean something and low enough that one unreachable branch of
an error path does not hold up a change. Two things follow from it:

- **A bare `uv run pytest` measures nothing.** Running one module's tests stays fast, and only
  the full gate checks the floor — a subset of the suite cannot meet a whole-project number.
- **Code that is genuinely hard to test gets restructured, not excused.** There are no
  `# pragma: no cover` markers in this repository. When `dexter.cli` sat at 61% because its
  drawing needed a terminal, the fix was to separate the decisions from the drawing
  (`interactive/menu.py`) and drive the rest through a fake window
  (`tests/cli/screen.py`) — which then found a real bug in how ESC left a submenu.

`./dx test` reports the same number per module and says whether the floor was met.

## Maintaining this file

Update `AGENTS.md` when structure, conventions, or tooling rules change — not for ordinary
code changes. Keep it under ~100 lines of substance: tables over prose, no preamble, and
only information that cannot be inferred from reading the code. A new module whose purpose
is not obvious from its name gets its own `AGENTS.md`.
