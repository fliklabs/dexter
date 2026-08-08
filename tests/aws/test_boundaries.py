"""The import boundaries `dexter.aws` claims, enforced rather than asserted in prose.

Three claims, each worth a test because each is one edit away from being false:

1. **boto3 reaches only the files that must speak it.** Everything else works in dexter's own
   types, which is what keeps `ClientError` off every consumer's `except` clause.
2. **No boto3 type appears on a public signature.** The module's central promise.
3. **Nothing here imports another dexter module.** `dexter.aws` sits beside `dexter.notification`
   in the graph, not above it — the SES adapter points the other way, from `dexter/notification/
   ses/`.
"""

import ast
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import dexter.aws

PACKAGE = Path(__file__).resolve().parents[2] / "dexter" / "aws"
SDK = {"boto3", "botocore"}
ALLOWED_SDK_FILES = {
    "session.py",  # builds every client
    "_calling.py",  # translates every failure
    "_items.py",  # the serializer, and `Binary`
    "_expressions.py",  # the condition builder
    "client.py",  # each client catches the codes only it can interpret
    "parameters.py",
    "secrets.py",
    "ses.py",
}
"""Files that may name the SDK, and why each one has to.

A whitelist rather than a rule, because the reason differs per file and a rule that covered all
of them would be too loose to catch a tenth. Adding a name here should take an argument.
"""


def imported_modules(source: Path) -> set[str]:
    """Every absolute module a file imports from, by dotted name.

    Read from the syntax tree rather than the text, so that prose naming a package — these
    docstrings discuss boto3 constantly — is not mistaken for a dependency.
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


def exported() -> list[Any]:
    """Every name `dexter.aws` re-exports."""
    return [
        getattr(dexter.aws, name)
        for name in dir(dexter.aws)
        if not name.startswith("_")
    ]


class TestTheSdkBoundary:
    def test_the_package_has_modules_to_check(self) -> None:
        assert modules()

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_only_the_named_files_import_the_sdk(self, source: Path) -> None:
        if source.name in ALLOWED_SDK_FILES:
            return
        offending = distributions(source) & SDK
        assert not offending, (
            f"{source.name} imports {offending}. Either it belongs in ALLOWED_SDK_FILES with a "
            f"reason, or the SDK has escaped the boundary."
        )

    def test_the_session_really_does_import_it(self) -> None:
        """The negative check alone would pass if the module were simply broken."""
        assert distributions(PACKAGE / "session.py") & SDK

    @pytest.mark.parametrize("source", modules(), ids=lambda path: path.name)
    def test_nothing_imports_another_dexter_module(self, source: Path) -> None:
        """`dexter.aws` is a peer of the other modules, never a consumer of one.

        The SES notifier points the other way, from `dexter/notification/ses/`, so a worker
        sending through `SesClient` pulls in no notification module.
        """
        allowed = {"dexter.commons", "dexter.dependency_injection"}
        reached = {
            name for name in imported_modules(source) if name.startswith("dexter.")
        }
        assert reached <= allowed, f"{source} imports {reached - allowed}"


class TestThePublicSurface:
    @pytest.mark.parametrize(
        "exported_name",
        [name for name in dir(dexter.aws) if not name.startswith("_")],
    )
    def test_no_boto3_type_is_re_exported(self, exported_name: str) -> None:
        """**The module's central promise**, checked against the objects rather than the prose."""
        value = getattr(dexter.aws, exported_name)
        module = getattr(value, "__module__", "")
        assert not module.startswith(("boto3", "botocore", "mypy_boto3")), (
            f"dexter.aws exports {exported_name}, which is a {module} type"
        )

    def test_no_public_method_annotates_a_boto3_type(self) -> None:
        """Reading the annotations rather than the source, so a re-export cannot hide one.

        `AwsSession`'s client properties are the deliberate exception: they exist to hand the
        typed clients to this module's own files, and are annotated under `TYPE_CHECKING` so
        nothing resolves at run time anyway.
        """
        offenders: list[str] = []
        for value in exported():
            if not inspect.isclass(value) or value is dexter.aws.AwsSession:
                continue
            for name, member in vars(value).items():
                if name.startswith("_") or not callable(member):
                    continue
                try:
                    hints = get_type_hints(member)
                except NameError, TypeError:
                    # An annotation that will not resolve at run time is a different concern
                    # from whether it names boto3, and `warn_unreachable` already covers it.
                    continue
                offenders.extend(
                    f"{value.__name__}.{name}"
                    for hint in hints.values()
                    if "boto3" in str(hint) or "botocore" in str(hint)
                )
        assert not offenders


class TestWhatImportingPullsIn:
    def test_importing_dexter_aws_does_not_import_another_dexter_module(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.aws; "
                "print(sorted({m.split('.')[1] for m in sys.modules "
                "if m.startswith('dexter.')} "
                "- {'aws', 'commons', 'dependency_injection'}))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "[]", (
            f"importing dexter.aws dragged in {result.stdout.strip()}"
        )

    def test_importing_dexter_aws_does_import_boto3(self) -> None:
        """It builds clients, so it must. The point of the boundary is *where*, not whether."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter.aws; print('boto3' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "True"
