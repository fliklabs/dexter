"""Reading resolved versions out of a lock file and writing them back as floors.

`dexter.tools.pins` **rewrites a project's `pyproject.toml`**, and it ships — so a bug here
damages the one file every other tool reads, in someone else's repository. That is why a
development-time helper is tested as closely as anything that runs in a request.
"""

from pathlib import Path

import pytest

from dexter.tools.pins import (
    Change,
    declared,
    locked,
    main,
    moved,
    normalise,
    raise_floors,
    raised,
    rewrite,
)

LOCK = """
version = 1

[[package]]
name = "click"
version = "8.4.2"

[[package]]
name = "pytest-cov"
version = "7.1.0"

[[package]]
name = "rich"
version = "15.0.0"
"""

PYPROJECT = """
[project]
dependencies = [
    # Why this floor is what it is.
    "click>=8.3",
    "rich>=14",
]

[dependency-groups]
dev = [{ include-group = "test" }]
test = ["pytest-cov>=7"]
"""


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text(LOCK, encoding="utf-8")
    return path


class TestReadingALock:
    def test_every_package_is_read(self, lock: Path) -> None:
        assert locked(lock) == {
            "click": "8.4.2",
            "pytest-cov": "7.1.0",
            "rich": "15.0.0",
        }

    def test_names_are_normalised(self) -> None:
        """`pytest_cov`, `pytest.cov` and `Pytest-Cov` are one package."""
        assert normalise("Pytest_Cov") == "pytest-cov"
        assert normalise("pytest.cov") == "pytest-cov"


class TestReadingRequirements:
    def test_runtime_and_group_requirements_are_both_found(self) -> None:
        assert declared(PYPROJECT) == ["click>=8.3", "rich>=14", "pytest-cov>=7"]

    def test_an_included_group_is_not_a_requirement(self) -> None:
        """`{ include-group = "test" }` is a table, and rewriting it would be nonsense."""
        assert not any("include-group" in entry for entry in declared(PYPROJECT))


class TestRaisingFloors:
    def test_each_floor_becomes_the_resolved_version(self, lock: Path) -> None:
        changes, _ = raised(PYPROJECT, locked(lock))

        assert [(change.name, change.old, change.new) for change in changes] == [
            ("click", "8.3", "8.4.2"),
            ("rich", "14", "15.0.0"),
            ("pytest-cov", "7", "7.1.0"),
        ]

    def test_a_floor_already_at_the_resolved_version_is_not_a_change(self) -> None:
        changes, _ = raised(
            '[project]\ndependencies = ["click>=8.4.2"]', {"click": "8.4.2"}
        )

        assert changes == []

    def test_a_package_missing_from_the_lock_is_left_alone(self) -> None:
        changes, _ = raised('[project]\ndependencies = ["click>=8.3"]', {})

        assert changes == []

    @pytest.mark.parametrize(
        "requirement",
        [
            "uvicorn[standard]>=0.34",
            "httpx>=0.28,<1.0",
            'click>=8.3; python_version < "3.15"',
            "pytest==8.3",
        ],
    )
    def test_anything_that_is_not_a_plain_floor_is_reported_not_guessed(
        self, requirement: str
    ) -> None:
        """Extras, ranges, markers and exact pins each mean something a rewrite would lose."""
        # Single-quoted so a marker containing double quotes is still valid TOML here.
        text = f"[project]\ndependencies = ['{requirement}']"
        changes, skipped = raised(
            text, {"uvicorn": "1", "httpx": "1", "click": "1", "pytest": "1"}
        )

        assert changes == []
        assert skipped == [requirement]


class TestRewriting:
    def test_the_floor_is_replaced(self, lock: Path) -> None:
        changes, _ = raised(PYPROJECT, locked(lock))
        result = rewrite(PYPROJECT, changes)

        assert '"click>=8.4.2"' in result
        assert '"rich>=15.0.0"' in result
        assert '"pytest-cov>=7.1.0"' in result

    def test_comments_survive(self, lock: Path) -> None:
        """The reasoning beside each floor is the reason this edits text, not a parsed document."""
        changes, _ = raised(PYPROJECT, locked(lock))

        assert "# Why this floor is what it is." in rewrite(PYPROJECT, changes)

    def test_nothing_else_moves(self, lock: Path) -> None:
        changes, _ = raised(PYPROJECT, locked(lock))
        result = rewrite(PYPROJECT, changes)

        assert result.count("\n") == PYPROJECT.count("\n")
        assert "[dependency-groups]" in result

    def test_either_quote_style_is_rewritten(self) -> None:
        """TOML accepts both, so aborting on one of them would be a needless failure."""
        result = rewrite("deps = ['click>=8.3']", [Change("click", "8.3", "8.4")])

        assert result == "deps = ['click>=8.4']"

    def test_a_requirement_that_is_not_there_verbatim_raises(self) -> None:
        """Better to stop than to write a floor into a file that never declared it.

        Spacing counts: `click >= 8.3` is the same requirement to a parser and a different
        string to a replace, and guessing which one the author meant is not this file's job.
        """
        with pytest.raises(LookupError):
            rewrite('dependencies = ["click >= 8.3"]', [Change("click", "8.3", "8.4")])


class TestComparingLocks:
    def test_it_reports_what_moved(self) -> None:
        changes = moved({"click": "8.3.0"}, {"click": "8.4.2"})

        assert [(c.name, c.old, c.new) for c in changes] == [
            ("click", "8.3.0", "8.4.2")
        ]

    def test_an_unchanged_package_is_not_reported(self) -> None:
        assert moved({"click": "8.4.2"}, {"click": "8.4.2"}) == []

    def test_a_newly_added_package_is_reported(self) -> None:
        """A transitive dependency that appeared is a change worth seeing."""
        changes = moved({}, {"anyio": "4.14.2"})

        assert [(c.name, c.old, c.new) for c in changes] == [("anyio", "-", "4.14.2")]

    def test_a_removed_package_is_not_an_upgrade(self) -> None:
        assert moved({"six": "1.16.0"}, {}) == []


class TestAgainstAProject:
    """The whole point of shipping this: it works on a project that is not dexter's own.

    `__file__` would resolve to the consumer's `site-packages` once installed, so a path taken
    from it would rewrite nothing, or the wrong thing. Every path is the caller's.
    """

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        (tmp_path / "uv.lock").write_text(LOCK, encoding="utf-8")
        return tmp_path

    def test_it_reads_the_project_it_is_given(self, project: Path) -> None:
        changes, _ = raise_floors(project)

        assert [change.name for change in changes] == ["click", "rich", "pytest-cov"]

    def test_reporting_does_not_write(self, project: Path) -> None:
        raise_floors(project)

        assert (project / "pyproject.toml").read_text(encoding="utf-8") == PYPROJECT

    def test_writing_writes(self, project: Path) -> None:
        raise_floors(project, write=True)

        assert '"click>=8.4.2"' in (project / "pyproject.toml").read_text(
            encoding="utf-8"
        )

    def test_it_does_not_touch_the_lock(self, project: Path) -> None:
        """Raising a floor is a claim about what is acceptable, not a re-resolution."""
        raise_floors(project, write=True)

        assert (project / "uv.lock").read_text(encoding="utf-8") == LOCK


class TestCommandLine:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        (tmp_path / "uv.lock").write_text(LOCK, encoding="utf-8")
        return tmp_path

    def test_floors_reports(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["floors", str(project)]) == 0
        assert "click" in capsys.readouterr().out

    def test_floors_writes_only_when_asked(self, project: Path) -> None:
        main(["floors", str(project)])
        assert (project / "pyproject.toml").read_text(encoding="utf-8") == PYPROJECT

        main(["floors", "--write", str(project)])
        assert (project / "pyproject.toml").read_text(encoding="utf-8") != PYPROJECT

    def test_floors_defaults_to_the_working_directory(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(project)

        assert main(["floors"]) == 0
        assert "click" in capsys.readouterr().out

    def test_a_project_already_current_says_so(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["floors", "--write", str(project)])
        capsys.readouterr()

        main(["floors", str(project)])

        assert "already the resolved version" in capsys.readouterr().out

    def test_changes_reports_what_moved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old = tmp_path / "old.lock"
        old.write_text(LOCK.replace('version = "8.4.2"', 'version = "8.3.0"'), "utf-8")
        new = tmp_path / "new.lock"
        new.write_text(LOCK, encoding="utf-8")

        assert main(["changes", str(old), str(new)]) == 0
        output = capsys.readouterr().out
        assert "8.3.0" in output
        assert "1 package(s) changed" in output

    def test_changes_between_identical_locks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lock = tmp_path / "uv.lock"
        lock.write_text(LOCK, encoding="utf-8")

        main(["changes", str(lock), str(lock)])

        assert "nothing moved" in capsys.readouterr().out

    def test_a_requirement_it_will_not_touch_is_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["uvicorn[standard]>=0.34"]', encoding="utf-8"
        )
        (tmp_path / "uv.lock").write_text(LOCK, encoding="utf-8")

        main(["floors", str(tmp_path)])

        assert "left alone" in capsys.readouterr().out

    @pytest.mark.parametrize("argv", [[], ["nonsense"], ["changes", "only-one"]])
    def test_anything_else_is_usage(
        self, argv: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(argv) == 2
        assert "usage" in capsys.readouterr().out
