"""The import boundary, which is the protocol seam.

`dexter.api` is transport-agnostic and `dexter.api.http` is the adapter. That claim is only
worth making if something enforces it, so this file does — an AST walk over the package, plus
a subprocess check that importing the core really does leave the web framework out.

The negative check alone would pass if the adapter were simply broken, so the positive one is
here too. The same pairing guards `dexter.cli` against pulling in `curses`.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "dexter" / "api"
TRANSPORT = {"fastapi", "starlette"}


def imported_modules(source: Path) -> set[str]:
    """Every absolute module a file imports from, by dotted name.

    Read from the syntax tree rather than the text, so that prose naming a module — this
    package's docstrings discuss `dexter.cqrs` at some length — is not mistaken for a
    dependency on it. Relative imports are excluded: they are by definition within `dexter.api`.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def distributions(source: Path) -> set[str]:
    """Every top-level distribution a file imports from."""
    return {name.split(".")[0] for name in imported_modules(source)}


def modules() -> list[Path]:
    """Every source file in the package."""
    return sorted(PACKAGE.rglob("*.py"))


class TestTheSeamIsReal:
    def test_the_package_has_modules_to_check(self) -> None:
        assert modules()

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_only_the_http_adapter_imports_a_web_framework(self, source: Path) -> None:
        if source.parent.name == "http":
            return
        offending = distributions(source) & TRANSPORT
        assert not offending, (
            f"{source} imports {offending}, which is the adapter's job"
        )

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_nothing_imports_another_dexter_module(self, source: Path) -> None:
        """Only `commons` and the container. An application may use CQRS; this module may not."""
        allowed = {"dexter.commons", "dexter.dependency_injection"}
        reached = {
            name for name in imported_modules(source) if name.startswith("dexter.")
        }
        assert reached <= allowed, f"{source} imports {reached - allowed}"


class TestWhatImportingPullsIn:
    def test_importing_the_core_does_not_import_a_web_framework(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.api; "
                "print(any(m in sys.modules for m in ('fastapi', 'starlette')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.api pulled in a web framework; the seam has been broken"
        )

    def test_importing_the_adapter_does(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.api.http; print('fastapi' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "True"
