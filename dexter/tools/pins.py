"""Reading resolved versions out of `uv.lock` and writing them back as declared floors.

`upgrade.sh` is the orchestration — back up, resolve, verify, keep or revert. This is the one
part of it that has to understand a file format, and it is separated for that reason alone.

**Standard library only, on purpose.** This runs in the middle of an upgrade, at the one moment
the environment is least trustworthy: `click` and `rich` are themselves being replaced, and a
helper whose job is to undo that must not depend on the thing being changed.

**The floor is taken from the lock, never from an index.** uv has already resolved a set that
satisfies `requires-python` and every other constraint; reusing its answer means a floor can
only ever be a version that demonstrably resolves. Asking PyPI for "the latest" separately
invites writing a floor that nothing can actually install.

Floors stay `>=` rather than becoming `==`. dexter is a library, and a library that pins
exactly is one a consumer cannot install beside anything else that disagrees; the exact set
belongs in `uv.lock`, which is what a lock file is for.
"""

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "uv.lock"

_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*>=\s*([0-9][^,;\s\]]*)$")
"""A requirement this can rewrite: one name, one `>=`, one version, nothing else.

Anything with extras, markers or a second constraint is left alone and reported. Rewriting a
requirement means understanding all of it, and half-understanding one is how a marker quietly
disappears.
"""


class Change:
    """One version that moved."""

    __slots__ = ("name", "new", "old")

    def __init__(self, name: str, old: str, new: str) -> None:
        """Record a package and the versions on either side of the change."""
        self.name = name
        self.old = old
        self.new = new

    def __str__(self) -> str:
        return f"  {self.name:<24} {self.old:>12} → {self.new}"


def normalise(name: str) -> str:
    """The PEP 503 form of a package name, so `pytest-cov` and `pytest_cov` are one thing."""
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(path: Path) -> dict[str, str]:
    """Every package in a lock file, by normalised name, with the version resolved for it."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    packages = data.get("package", [])
    return {
        normalise(str(entry["name"])): str(entry["version"])
        for entry in packages
        if "name" in entry and "version" in entry
    }


def declared(text: str) -> list[str]:
    """Every requirement string in `pyproject.toml`, runtime and development alike.

    Dependency groups may include other groups (`{ include-group = "test" }`); those are
    tables rather than strings and are not requirements, so they are skipped.
    """
    config = tomllib.loads(text)
    requirements = list(config.get("project", {}).get("dependencies", []))
    for group in config.get("dependency-groups", {}).values():
        requirements.extend(entry for entry in group if isinstance(entry, str))
    return [str(requirement) for requirement in requirements]


def raised(text: str, versions: dict[str, str]) -> tuple[list[Change], list[str]]:
    """What the floors would become, and which requirements are too complex to touch."""
    changes: list[Change] = []
    skipped: list[str] = []

    for requirement in declared(text):
        match = _REQUIREMENT.match(requirement)
        if match is None:
            skipped.append(requirement)
            continue
        name, floor = match.group(1), match.group(2)
        resolved = versions.get(normalise(name))
        if resolved is not None and resolved != floor:
            changes.append(Change(name, floor, resolved))
    return changes, skipped


def rewrite(text: str, changes: list[Change]) -> str:
    """Apply the new floors to the file's own text.

    The text is edited rather than the parsed document written back, because `pyproject.toml`
    carries the reasoning for every one of these numbers in comments beside them — and every
    TOML writer that round-trips a document is one more dependency this file refuses to have.
    Each replacement is an exact quoted string, so it can only match the requirement it means.
    """
    for change in changes:
        # Both quote styles, because TOML accepts either and a file written with one would
        # otherwise abort the rewrite for no reason.
        for quote in ('"', "'"):
            before = f"{quote}{change.name}>={change.old}{quote}"
            if before in text:
                text = text.replace(
                    before, f"{quote}{change.name}>={change.new}{quote}"
                )
                break
        else:
            message = f"{change.name}>={change.old} is not in pyproject.toml as written"
            raise LookupError(message)
    return text


def moved(before: dict[str, str], after: dict[str, str]) -> list[Change]:
    """Every package whose resolved version differs between two lock files."""
    changes = [
        Change(name, before[name], version)
        for name, version in sorted(after.items())
        if name in before and before[name] != version
    ]
    changes.extend(
        Change(name, "-", version)
        for name, version in sorted(after.items())
        if name not in before
    )
    return changes


# ── entry point ──────────────────────────────────────────────────────


def _floors(write: bool) -> int:
    """Report, and optionally apply, the floors implied by the current lock."""
    text = PYPROJECT.read_text(encoding="utf-8")
    changes, skipped = raised(text, locked(LOCK))

    for requirement in skipped:
        print(f"  left alone (not a plain floor): {requirement}")

    if not changes:
        print("  every declared floor is already the resolved version")
        return 0

    for change in changes:
        print(str(change))
    if write:
        PYPROJECT.write_text(rewrite(text, changes), encoding="utf-8")
    return 0


def _changes(old: Path, new: Path) -> int:
    """Report what moved between two lock files."""
    changes = moved(locked(old), locked(new))
    if not changes:
        print("  nothing moved")
        return 0
    for change in changes:
        print(str(change))
    print(f"  {len(changes)} package(s) changed")
    return 0


_LOCK_PAIR = 2
"""`changes` takes two lock files: the one from before the upgrade and the one after."""


def main(argv: list[str]) -> int:
    """Dispatch the two things this does. Not click: see the module docstring."""
    if argv[:1] == ["floors"]:
        return _floors(write="--write" in argv)
    if argv[:1] == ["changes"] and len(argv[1:]) == _LOCK_PAIR:
        return _changes(Path(argv[1]), Path(argv[2]))
    print("usage: pins floors [--write] | pins changes <old-lock> <new-lock>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
