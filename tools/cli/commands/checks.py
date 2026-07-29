"""Running the test suite, and the repository's verification gate.

The two are deliberately different jobs. `test` is the feedback loop: it runs pytest directly
and reports what happened — pass and fail counts, how long it took, the slowest tests, and
coverage. `verify` is the gate: it shells out to `./verify.sh`, unchanged, which is exactly
what CI runs. Neither reimplements the other.

Statistics come from machine-readable output rather than from scraping pytest's terminal
report, which changes between releases and is the wrong thing to build on.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import click

from dexter.cli import CliConsole, inject
from dexter.dependency_injection import Container

from ..paths import REPO_ROOT, VERIFY

_SLOWEST = 5
"""How many of the slowest tests to list. Enough to spot a problem, few enough to skim."""


class Results:
    """What one pytest run produced."""

    __slots__ = ("duration", "failures", "outcomes", "slowest")

    def __init__(
        self,
        outcomes: dict[str, int],
        duration: float,
        slowest: list[tuple[str, float]],
        failures: list[tuple[str, str]],
    ) -> None:
        """Record the counts, the total time, the slowest tests, and what failed."""
        self.outcomes = outcomes
        self.duration = duration
        self.slowest = slowest
        self.failures = failures

    @property
    def total(self) -> int:
        """How many tests ran."""
        return sum(self.outcomes.values())

    @property
    def passed(self) -> int:
        """How many succeeded."""
        return self.outcomes.get("passed", 0)


@click.command("test")
@click.option(
    "--coverage/--no-coverage", default=True, help="Measure coverage of `dexter`."
)
@click.option("--path", default="tests", help="Only run tests under this path.")
@click.option(
    "-k", "selector", default="", help="Only run tests matching this expression."
)
@inject
async def test(scope: Container, coverage: bool, path: str, selector: str) -> int:
    """Run the test suite and report pass rate, timing and coverage."""
    console = await scope.resolve(CliConsole)

    with tempfile.TemporaryDirectory() as directory:
        junit = Path(directory) / "results.xml"
        coverage_file = Path(directory) / "coverage.json"

        command = ["uv", "run", "pytest", path, f"--junitxml={junit}", "-q"]
        if selector:
            command.extend(["-k", selector])
        if coverage:
            command.extend(
                ["--cov=dexter", f"--cov-report=json:{coverage_file}", "--cov-report="]
            )

        console.detail(f"$ {' '.join(command)}")
        exit_code = await _run(command)

        results = _read_junit(junit)
        _report(console, results)
        if coverage:
            _report_coverage(console, coverage_file)

    return exit_code


@click.command("verify")
@click.option(
    "--fix", is_flag=True, help="Format and autofix in place before checking."
)
@inject
async def verify(scope: Container, fix: bool) -> int:
    """Run the full gate: format, lint, type-check and test. What CI runs."""
    console = await scope.resolve(CliConsole)
    command = [str(VERIFY), "-v"] + (["--fix"] if fix else [])

    console.detail(f"$ {' '.join(command)}")
    exit_code = await _run(command)

    if exit_code == 0:
        console.ok("verify passed")
    else:
        console.error(f"verify failed (exit {exit_code})")
    return exit_code


# ── internals ────────────────────────────────────────────────────────


async def _run(command: list[str]) -> int:
    """Run a subprocess from the repository root, streaming its output through.

    Output is written rather than captured so it reaches whatever is listening — the terminal
    when scripted, and the menu's redirected stream when not.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None  # noqa: S101 - guaranteed by PIPE above
    async for line in process.stdout:
        click.echo(line.decode(errors="replace"), nl=False)
    return await process.wait()


def _read_junit(report: Path) -> Results:
    """Parse pytest's JUnit XML into counts, timings and failures."""
    if not report.is_file():
        return Results({}, 0.0, [], [])

    root = ElementTree.parse(report).getroot()  # noqa: S314 - pytest's own output
    outcomes: dict[str, int] = {}
    timings: list[tuple[str, float]] = []
    failures: list[tuple[str, str]] = []
    duration = 0.0

    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")
        elapsed = float(case.get("time", "0") or 0)
        duration += elapsed
        timings.append((name, elapsed))

        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed"
                failures.append((name, (child.get("message") or child.tag)))
            elif child.tag == "skipped":
                outcome = "skipped"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    timings.sort(key=lambda entry: entry[1], reverse=True)
    return Results(outcomes, duration, timings[:_SLOWEST], failures)


def _report(console: CliConsole, results: Results) -> None:
    """Render the run's headline numbers."""
    console.heading("results")
    if results.total == 0:
        console.warn("No tests ran.")
        return

    failed = results.outcomes.get("failed", 0)
    skipped = results.outcomes.get("skipped", 0)
    rate = results.passed / results.total * 100

    table = console.table("Passed", "Failed", "Skipped", "Rate", "Duration")
    table.add_row(
        f"[green]{results.passed}[/]",
        f"[red]{failed}[/]" if failed else "0",
        f"[yellow]{skipped}[/]" if skipped else "0",
        f"{rate:.1f}%",
        f"{results.duration:.2f}s",
    )
    console.print(table)

    if results.failures:
        console.heading("failures")
        for name, message in results.failures:
            console.error(name)
            console.detail(f"    {message.splitlines()[0][:100]}")

    if results.slowest and not results.failures:
        console.heading("slowest")
        slowest = console.table("Test", "Time")
        for name, elapsed in results.slowest:
            slowest.add_row(f"[dim]{name}[/]", f"{elapsed * 1000:.0f}ms")
        console.print(slowest)


def _report_coverage(console: CliConsole, report: Path) -> None:
    """Render coverage overall and per module."""
    if not report.is_file():
        console.warn("No coverage report was produced.")
        return

    data = json.loads(report.read_text(encoding="utf-8"))
    total = float(data.get("totals", {}).get("percent_covered", 0.0))

    console.heading("coverage")
    table = console.table("Module", "Statements", "Missing", "Covered")
    for name, entry in sorted(_by_module(data).items()):
        table.add_row(
            f"[cyan]{name}[/]",
            str(entry["statements"]),
            str(entry["missing"]) if entry["missing"] else "-",
            _percentage(entry["covered"]),
        )
    console.print(table)
    console.print(f"  [bold]total[/]  {_percentage(total)}")


def _by_module(data: dict[str, object]) -> dict[str, dict[str, float]]:
    """Roll per-file coverage up to one row per dexter module."""
    files = data.get("files", {})
    assert isinstance(files, dict)  # noqa: S101 - shape of coverage's own report

    modules: dict[str, dict[str, float]] = {}
    for path, entry in files.items():
        parts = Path(path).parts
        # `dexter/cqrs/bus.py` rolls up to `dexter.cqrs`; `dexter/__init__.py` has no module
        # below the top level, so it stays `dexter`.
        name = (
            ".".join(parts[:2])
            if len(parts) > 1 and not parts[1].endswith(".py")
            else parts[0]
        )
        summary = entry["summary"]
        bucket = modules.setdefault(
            name, {"statements": 0, "missing": 0, "covered": 0.0}
        )
        bucket["statements"] += summary["num_statements"]
        bucket["missing"] += summary["missing_lines"]

    for bucket in modules.values():
        counted = bucket["statements"]
        bucket["covered"] = (
            100.0 * (counted - bucket["missing"]) / counted if counted else 100.0
        )
    return modules


def _percentage(value: float) -> str:
    """Colour a percentage by how comfortable it is."""
    colour = "green" if value >= 90 else "yellow" if value >= 75 else "red"  # noqa: PLR2004
    return f"[{colour}]{value:.1f}%[/]"
