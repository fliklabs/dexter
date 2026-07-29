"""Where things are, worked out once.

Every command that shells out needs the repository root, and none of them should guess at it
from the current working directory — `./dx` can be run from anywhere.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The checkout this CLI belongs to."""

VERIFY = REPO_ROOT / "verify.sh"
