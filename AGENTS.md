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
| `dexter.dependency_injection` | Scaffolded, no public API | DI container |
| `dexter.cqrs` | Planned | Commands, queries, events and their buses |
| `dexter.application` | Planned | Application composition and wiring |
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

## Naming

- `snake_case` for modules and packages. Singular for one concept (`container.py`),
  plural for a collection of like things (`errors.py`, `models.py`).
- One public class per file where practical, file named after it: `container_builder.py`
  defines `ContainerBuilder`.
- **No `utils.py` grab-bags.** Name utilities after what they operate on:
  `type_utils.py`, `string_utils.py`.
- Leading underscore (`_internal.py`) for module-private helpers that are not re-exported.

## `dexter.commons`

Two constraints beyond the normal rules:

1. **It must never import from another dexter module.** It is the bottom of the
   dependency graph; a cycle here would be unfixable.
2. **Nothing enters until at least two modules need it.** Speculative shared code is how
   a commons package rots. A one-consumer helper stays in the module that uses it until a
   second consumer appears.

Rule 2 is currently applied to itself: `commons` holds only `errors.py`, because that is
the only thing with a real second consumer. Files like `types.py` or `type_utils.py` get
created when something actually needs them — not in advance.

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

**Read annotations with `typing.get_type_hints()`, never raw `__annotations__` or
`inspect.signature(...).parameters[...].annotation`.**

Raw annotations are plain strings whenever the defining module uses
`from __future__ import annotations`, which silently breaks any runtime type
introspection. `get_type_hints()` resolves them to real objects either way, so dexter
imposes no restriction on how consumers write their own annotations.

Two traps if you introspect constructors:

- The dict returned by `get_type_hints()` is **not** in signature order. Iterate
  `inspect.signature(...).parameters` and look hints up by name; never zip the two.
- A class with no `__init__` of its own reports `(self, /, *args, **kwargs)`. Check
  `"__init__" in vars(cls)` before treating those parameters as dependencies.

## Tooling

`uv` for dependencies, `ruff` for formatting and linting, `mypy --strict` for types,
`pytest` for tests. Configuration lives entirely in `pyproject.toml`.

```bash
uv sync                       # create .venv, install dev dependencies
./verify.sh --fix             # format, lint, type-check, test
./verify.sh                   # same, without writing changes
uv run pytest tests/commons   # one module's tests
```

**A change is not done until `./verify.sh` exits 0.**

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

## Maintaining this file

Update `AGENTS.md` when structure, conventions, or tooling rules change — not for ordinary
code changes. Keep it under ~100 lines of substance: tables over prose, no preamble, and
only information that cannot be inferred from reading the code. A new module whose purpose
is not obvious from its name gets its own `AGENTS.md`.
