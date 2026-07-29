"""Reading resolved versions out of a uv lock file and writing them back as declared floors.

This is the part of a dependency upgrade that has to understand a file format. The orchestration
around it — back up, resolve, verify, keep or revert — stays in the repository that runs it,
because what "verify" means differs per project. `upgrade.sh` in dexter's own checkout is the
worked example; nothing here assumes it exists.

**Nothing is resolved from `__file__`.** Every path is given by the caller and defaults to the
working directory, which is what makes this usable from a repository other than the one it was
written in: installed into a consumer's environment, `__file__` points at their `site-packages`,
and a tool that located the manifest that way would rewrite the wrong project — or, far more
likely, nothing at all.

**Standard library only, on purpose.** This runs in the middle of an upgrade, at the one moment
the environment is least trustworthy: `click` and `rich` may themselves be part-installed, and a
helper whose job includes undoing that must not depend on the thing being changed.

**Floors are taken from the lock, never from an index.** uv has already resolved a set that
satisfies `requires-python` and every other constraint; reusing its answer means a floor can only
ever be a version that demonstrably resolves. Asking PyPI for "the latest" separately invites
writing a floor that nothing can actually install.

Floors stay `>=` rather than becoming `==`. A library that pins exactly is one a consumer cannot
install beside anything that disagrees; the exact set belongs in the lock file, which is what a
lock file is for.
"""

import re
import sys
import tomllib
from pathlib import Path

MANIFEST = "pyproject.toml"
LOCKFILE = "uv.lock"

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
    """Every requirement string in a manifest, runtime and development alike.

    Dependency groups may include other groups (`{ include-group = "test" }`); those are tables
    rather than strings and are not requirements, so they are skipped.
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
    """Apply new floors to a manifest's own text.

    The text is edited rather than a parsed document written back, because a manifest carries the
    reasoning for its numbers in comments beside them — and every TOML writer that round-trips a
    document is one more dependency this module refuses to have. Each replacement is an exact
    quoted string, so it can only match the requirement it means.
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
            message = f"{change.name}>={change.old} is not in {MANIFEST} as written"
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


def raise_floors(
    project: Path, *, write: bool = False
) -> tuple[list[Change], list[str]]:
    """Bring a project's declared floors up to what its own lock resolved.

    Returns what would change and what was left alone. Nothing is written unless asked, so one
    call both reports a plan and applies it.
    """
    manifest = project / MANIFEST
    text = manifest.read_text(encoding="utf-8")
    changes, skipped = raised(text, locked(project / LOCKFILE))
    if write and changes:
        manifest.write_text(rewrite(text, changes), encoding="utf-8")
    return changes, skipped


# ── command line ─────────────────────────────────────────────────────

USAGE = """usage:
  python -m dexter.tools.pins floors [--write] [<project-dir>]
  python -m dexter.tools.pins changes <old-lock> <new-lock>

floors   raise every declared `>=` floor to the version its lock resolved
changes  report what moved between two lock files
"""


def _floors(argv: list[str]) -> int:
    """Report, and optionally apply, the floors implied by a project's lock."""
    named = [argument for argument in argv if not argument.startswith("-")]
    project = Path(named[0]) if named else Path.cwd()

    changes, skipped = raise_floors(project, write="--write" in argv)
    for requirement in skipped:
        print(f"  left alone (not a plain floor): {requirement}")
    if not changes:
        print("  every declared floor is already the resolved version")
        return 0
    for change in changes:
        print(str(change))
    return 0


def _changes(argv: list[str]) -> int:
    """Report what moved between two lock files."""
    changes = moved(locked(Path(argv[0])), locked(Path(argv[1])))
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
        return _floors(argv[1:])
    if argv[:1] == ["changes"] and len(argv[1:]) == _LOCK_PAIR:
        return _changes(argv[1:])
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
