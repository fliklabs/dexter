"""Guards dexter's public import surface.

Every name a module re-exports is imported here, so a mis-edited ``__init__.py`` fails
the suite rather than a consumer's build.
"""

import subprocess
import sys

import dexter
from dexter.commons import DexterError
from dexter.dependency_injection import DependencyInjectionError


class TestTopLevelPackage:
    def test_exposes_a_version_string(self) -> None:
        assert isinstance(dexter.__version__, str)
        assert dexter.__version__

    def test_importing_dexter_does_not_import_framework_modules(self) -> None:
        """``import dexter`` must stay cheap and pull in no framework modules."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, dexter; "
                "print(sorted(m for m in sys.modules if m.startswith('dexter.')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "[]", (
            f"importing dexter dragged in submodules: {result.stdout.strip()}"
        )


class TestExceptionHierarchy:
    def test_every_module_error_descends_from_dexter_error(self) -> None:
        assert issubclass(DependencyInjectionError, DexterError)

    def test_dexter_error_is_an_exception(self) -> None:
        assert issubclass(DexterError, Exception)
