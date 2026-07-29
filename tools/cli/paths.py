"""Where things are, worked out once.

Every command that shells out needs the repository root, and none of them should guess at it
from the current working directory — `./dx` can be run from anywhere.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The checkout this CLI belongs to."""

EXAMPLES = REPO_ROOT / "examples"
VERIFY = REPO_ROOT / "verify.sh"


def example_names() -> tuple[str, ...]:
    """Every reference application in `examples/`, discovered rather than listed.

    A package with a `__main__.py` is a runnable example. Discovering them means a new one
    appears in the menu the moment it exists, and cannot be forgotten here.
    """
    if not EXAMPLES.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in EXAMPLES.iterdir()
            if entry.is_dir() and (entry / "__main__.py").is_file()
        )
    )
