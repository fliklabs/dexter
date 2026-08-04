"""The two import boundaries this module claims, and one negative check for each.

`dexter.iam` is transport-agnostic and `dexter.iam.api` is the adapter, exactly as
`dexter.api` is to `dexter.api.http`. And the whole of the JWT library lives behind one file,
so that the library's one piece of cryptography is somewhere a reader can find it.

Claims like these are only worth making if something enforces them, so this file does — an AST
walk over the package, plus subprocess checks that importing the core really does leave the
adapter out.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "dexter" / "iam"
CODEC = "jwt_codec.py"
ADAPTER = "api"


def imported_modules(source: Path) -> set[str]:
    """Every absolute module a file imports from, by dotted name.

    Read from the syntax tree rather than the text, so that prose naming a module — these
    docstrings discuss `dexter.api` at some length — is not mistaken for a dependency on it.
    Relative imports are excluded: they are by definition within `dexter.iam`.
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
    def test_only_the_codec_imports_a_jwt_library(self, source: Path) -> None:
        if source.name == CODEC:
            return
        assert "jwt" not in distributions(source), (
            f"{source} imports jwt; the whole of it belongs in {CODEC}"
        )

    def test_the_codec_really_does_import_one(self) -> None:
        """The check above would pass if the codec were simply broken."""
        assert "jwt" in distributions(PACKAGE / CODEC)

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_only_the_adapter_imports_the_api_module(self, source: Path) -> None:
        if source.parent.name == ADAPTER:
            return
        reached = {
            name for name in imported_modules(source) if name.startswith("dexter.")
        }
        allowed = {"dexter.commons", "dexter.dependency_injection"}
        assert reached <= allowed, f"{source} imports {reached - allowed}"

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_nothing_imports_a_web_framework(self, source: Path) -> None:
        """Not even the adapter. `dexter.api` is the only thing that knows one exists."""
        offending = distributions(source) & {"fastapi", "starlette"}
        assert not offending, f"{source} imports {offending}"


class TestWhatImportingPullsIn:
    def test_importing_the_core_does_not_import_the_api_module(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.iam; print('dexter.api' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.iam pulled in dexter.api; the seam has been broken"
        )

    def test_importing_the_adapter_does(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.iam.api; print('dexter.api' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "True"

    def test_importing_the_core_does_not_import_a_web_framework(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.iam.api; "
                "print(any(m in sys.modules for m in ('fastapi', 'starlette')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False"
