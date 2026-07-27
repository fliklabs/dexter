# dexter

A collection of independent, reusable application framework modules.

Each module under `dexter/` is self-contained and imported directly:

```python
from dexter.dependency_injection import Container
```

`import dexter` deliberately pulls in nothing but the version — importing the top
level never drags in every framework.

## Status

Early scaffolding. The repository structure, conventions and tooling are in place;
the framework modules themselves are not implemented yet.

| Module | Status |
| --- | --- |
| `dexter.commons` | Scaffolded — shared primitives |
| `dexter.dependency_injection` | Scaffolded — no public API yet |

## Install

```bash
uv add "dexter @ git+https://github.com/fliklabs/dexter"
```

`dexter` has no runtime dependencies.

## Development

Requires [uv](https://docs.astral.sh/uv/). Python is installed and pinned by uv —
you do not need a system Python.

```bash
uv sync                       # create .venv and install dev dependencies
./verify.sh --fix             # format, lint, type-check, test
./verify.sh                   # same, without writing changes
uv run pytest tests/commons   # run one module's tests
```

A change is not finished until `./verify.sh` exits 0.

See [AGENTS.md](./AGENTS.md) for repository structure and conventions.

## A note for consumers using mypy

`dexter` ships type information (PEP 561), so its types are visible to your type
checker with no stubs required.

Once the dependency injection module lands, its registration APIs will accept
`type[T]`. mypy rejects passing an abstract class or `Protocol` where `type[T]` is
expected (`error: Only concrete class can be given where "type[T]" is expected`,
error code `type-abstract`) — and most useful DI keys are abstract. Two remedies:

```python
container.register(Repository, implementation=SqlRepository)  # type: ignore[type-abstract]
```

or, project-wide in your own configuration:

```toml
[tool.mypy]
disable_error_code = ["type-abstract"]
```

## Licence

MIT — see [LICENSE](./LICENSE).
