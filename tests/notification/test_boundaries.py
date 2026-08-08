"""The import boundary, which is what makes `httpx` an optional dependency.

`dexter.notification` is provider-agnostic and `dexter.notification.resend` is the engine. The
claim is only worth making if something enforces it, so this file does — an AST walk over the
package, plus a subprocess check that importing the core really does leave the HTTP client out.

The negative check alone would pass if the engine were simply broken, so the positive one is
here too. The same pairing guards `dexter.api` against pulling in a web framework.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "dexter" / "notification"
ENGINE = "resend"
CLIENTS = {"httpx", "requests", "aiohttp", "urllib3"}


def imported_modules(source: Path) -> set[str]:
    """Every absolute module a file imports from, by dotted name.

    Read from the syntax tree rather than the text, so that prose naming a package — these
    docstrings discuss `httpx` and the provider's SDK — is not mistaken for a dependency.
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
    def test_only_the_engine_imports_an_http_client(self, source: Path) -> None:
        if source.parent.name in {ENGINE, "ses"}:
            return
        offending = distributions(source) & CLIENTS
        assert not offending, (
            f"{source} imports {offending}; that is what makes it an optional extra"
        )

    def test_the_engine_really_does_import_one(self) -> None:
        assert distributions(PACKAGE / ENGINE / "notifier.py") & CLIENTS

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_nothing_imports_the_provider_s_own_sdk(self, source: Path) -> None:
        """The SDK is synchronous and keys itself from a module global. See `notifier.py`."""
        assert "resend" not in distributions(source)

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_nothing_imports_another_dexter_module(self, source: Path) -> None:
        """Except the SES engine, which is an adapter over one.

        The allowance is scoped to `ses/` rather than granted to the module, so the core stays
        importable with no AWS anywhere in the process — which is what
        `TestWhatImportingPullsIn` then asserts by running it.
        """
        allowed = {"dexter.commons", "dexter.dependency_injection"}
        if source.parent.name == "ses":
            allowed |= {"dexter.aws"}
        reached = {
            name for name in imported_modules(source) if name.startswith("dexter.")
        }
        assert reached <= allowed, f"{source} imports {reached - allowed}"

    def test_the_ses_engine_really_does_import_aws(self) -> None:
        """The negative check above would pass if the engine were simply broken."""
        assert "dexter.aws" in imported_modules(PACKAGE / "ses" / "notifier.py")


class TestWhatImportingPullsIn:
    def test_importing_the_core_does_not_import_an_http_client(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.notification; print('httpx' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.notification pulled in httpx; the seam has been broken"
        )

    def test_importing_the_engine_does(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.notification.resend; print('httpx' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "True"

    def test_importing_the_core_does_not_import_aws(self) -> None:
        """What keeps the SES engine's cost inside the SES engine."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.notification; print('boto3' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", (
            "importing dexter.notification pulled in boto3; the seam has been broken"
        )
