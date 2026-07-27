"""Static typing guarantees, asserted by running mypy.

The point of the fluent binder is that a provider which cannot produce the key is a *type*
error, caught while writing the code. No runtime test can demonstrate that, so this module
runs mypy over deliberately-wrong bindings and asserts it fails — and over correct bindings
with abstract and `Protocol` keys, asserting it does not.

This costs a mypy invocation, which is slower than the rest of the suite. It is worth it:
without it, a future signature change could silently drop the guarantee the design was chosen
for.
"""

import functools
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_BAD_BINDINGS = '''\
"""Bindings that must not type-check."""

from dexter.dependency_injection import ContainerBuilder, Scope
from tests.dependency_injection.conftest import Greeter, Hello, Repository, SqlRepository


def returns_an_int() -> int:
    return 1


async def async_returns_an_int() -> int:
    return 1


builder = ContainerBuilder()
builder.register(Repository).to(SqlRepository, scope=Scope.Transient)
builder.register(Greeter).to(Hello, scope=Scope.Singleton)
builder.register(Repository).to(returns_an_int, scope=Scope.Transient)
builder.register(Repository).to(async_returns_an_int, scope=Scope.Transient)
builder.register(Greeter).to_instance(42)
builder.register(Repository).to(SqlRepository)
'''

_GOOD_BINDINGS = '''\
"""Bindings that must type-check, including abstract and Protocol keys."""

from dexter.dependency_injection import Container, ContainerBuilder, Scope
from tests.dependency_injection.conftest import (
    Db,
    Greeter,
    Hello,
    Repository,
    SqlRepository,
)


async def open_repository(db: Db) -> Repository:
    return SqlRepository(db)


builder = ContainerBuilder()
builder.register(Db).to(Db, scope=Scope.Singleton)
builder.register(Repository).to(SqlRepository, scope=Scope.Scoped)
builder.register(Greeter).to(Hello, scope=Scope.Singleton)
builder.register(Greeter).to_instance(Hello())


async def use(container: Container) -> str:
    repository = await container.resolve(Repository)
    greeter = await container.resolve(Greeter)
    maybe = await container.try_resolve(Db)
    return f"{repository.find()}{greeter.greet()}{maybe is None}"
'''


def run_mypy(source: str) -> subprocess.CompletedProcess[str]:
    """Type-check `source` as a standalone file, from the repository root."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "bindings.py"
        target.write_text(source, encoding="utf-8")
        return subprocess.run(  # noqa: S603 - fixed argv, path from tempfile
            [sys.executable, "-m", "mypy", "--strict", str(target)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )


@functools.cache
def bad_binding_errors() -> str:
    """Run mypy once over the bad bindings and return its output."""
    result = run_mypy(_BAD_BINDINGS)
    assert result.returncode != 0, f"mypy accepted bad bindings:\n{result.stdout}"
    return result.stdout


class TestBadBindingsAreRejected:
    def test_rejects_a_sync_provider_returning_the_wrong_type(self) -> None:
        assert 'incompatible type "Callable[[], int]"' in bad_binding_errors()

    def test_rejects_an_async_provider_returning_the_wrong_type(self) -> None:
        assert (
            'incompatible type "Callable[[], Coroutine[Any, Any, int]]"'
            in bad_binding_errors()
        )

    def test_rejects_an_instance_of_the_wrong_type(self) -> None:
        assert 'to_instance" of "Binder" has incompatible type "int"' in (
            bad_binding_errors()
        )

    def test_rejects_omitting_the_required_scope(self) -> None:
        assert 'Missing named argument "scope"' in bad_binding_errors()

    def test_reports_every_mistake_as_a_typing_error(self) -> None:
        output = bad_binding_errors()
        assert output.count("arg-type") >= 3
        assert "call-arg" in output


class TestGoodBindingsAreAccepted:
    def test_abstract_and_protocol_keys_need_no_suppression(self) -> None:
        result = run_mypy(_GOOD_BINDINGS)
        assert result.returncode == 0, result.stdout
        # The whole reason the key is widened: no consumer-side `type-abstract` suppression.
        assert "type-abstract" not in result.stdout
