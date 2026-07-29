#!/usr/bin/env bash
#
# Move every dependency to its latest resolvable version, and keep it only if the gate passes.
#
#   ./upgrade.sh             upgrade, verify, keep on success — revert on failure
#   ./upgrade.sh --lock-only move uv.lock forward but leave the declared floors alone
#   ./upgrade.sh --dry-run   show what would change, then put everything back
#   ./upgrade.sh -v          stream the gate's output as it runs
#
# `pyproject.toml` and `uv.lock` are restored on any failure, including Ctrl+C, and the
# environment is re-synced to match them — so a failed upgrade leaves nothing behind.
#
# What this does NOT touch: `.python-version`. The interpreter pin is a separate decision with
# its own procedure (`uv python pin`), and rolling it forward silently inside a dependency
# upgrade is how a project ends up on a Python nobody chose. See AGENTS.md.
#
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

LOCK_ONLY=0
DRY_RUN=0
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        --lock-only) LOCK_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -v | --verbose) VERBOSE=1 ;;
        -h | --help)
            sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/" >&2
    exit 1
fi

# Everything this may rewrite. A dirty working tree is fine: what is restored is exactly what
# was here when this started, not what was last committed — so an upgrade attempted on top of
# work in progress cannot discard it.
TRACKED=(pyproject.toml uv.lock)

BACKUP="$(mktemp -d "${TMPDIR:-/tmp}/dexter-upgrade-XXXXXX")"
KEEP=0

restore() {
    for file in "${TRACKED[@]}"; do
        cp "$BACKUP/$(basename "$file")" "$file"
    done
    # The files alone are not the state. Without this the environment still holds the versions
    # that just failed, and the next run of anything at all would use them.
    uv sync --quiet
}

finish() {
    local status=$?
    if [ "$KEEP" -eq 0 ] && [ -d "$BACKUP" ]; then
        echo ""
        echo "Reverting pyproject.toml and uv.lock, and re-syncing the environment..."
        restore
        echo "Reverted. Nothing was changed."
    fi
    rm -rf "$BACKUP"
    exit "$status"
}

interrupted() {
    echo "" >&2
    echo "Interrupted." >&2
    exit 130
}

# Two handlers, not one. bash defers a signal until the command it is running finishes, so a
# single `trap ... EXIT INT TERM` handler runs *after* the script has already carried on to the
# end and set KEEP=1 — the interrupt is then swallowed and a half-wanted upgrade is kept. This
# one exits instead, which reaches `finish` with KEEP still 0 and reverts. An interrupt arriving
# after the gate has passed keeps the upgrade, which is correct: the work was done and verified.
trap interrupted INT TERM
trap finish EXIT

for file in "${TRACKED[@]}"; do
    cp "$file" "$BACKUP/$(basename "$file")"
done

echo "=============================="
echo "Upgrading dependencies"
echo "=============================="
echo ""

echo "--- resolving the newest versions ---"
uv lock --upgrade

echo ""
echo "--- what moved ---"
uv run --quiet python -m tools.pins changes "$BACKUP/uv.lock" uv.lock

if [ "$LOCK_ONLY" -eq 0 ]; then
    echo ""
    echo "--- raising the declared floors to match ---"
    # Floors are raised from the lock rather than from an index, so a floor can only ever be a
    # version that has already been shown to resolve. They stay `>=`: dexter is a library, and
    # the exact set belongs in uv.lock. See tools/pins.py.
    uv run --quiet python -m tools.pins floors --write
    # pyproject.toml has changed, so the lock's record of it is stale. This re-locks without
    # `--upgrade`, which keeps the versions just resolved and only refreshes the metadata.
    uv lock
fi

echo ""
echo "--- installing them ---"
uv sync

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "Dry run: not verifying, and putting everything back."
    exit 0
fi

echo ""
echo "=============================="
echo "Verifying"
echo "=============================="

# The full gate, not just pytest. A dependency upgrade breaks lint and type-checking far more
# often than it breaks tests — `ruff` and `mypy` are themselves being upgraded here, and a new
# release of either can fail a file that no test would ever notice.
#
# Written out twice rather than built as an array: `"${array[@]}"` on an empty array is an
# unbound variable under `set -u` in bash 3.2, which is what macOS ships and what this ran into.
gate() {
    if [ "$VERBOSE" -eq 1 ]; then
        ./verify.sh -v
    else
        ./verify.sh
    fi
}

if ! gate; then
    echo ""
    echo "ERROR: the gate failed with the upgraded dependencies." >&2
    echo "       Nothing has been kept. Run ./upgrade.sh -v to see the failures in full," >&2
    echo "       or ./upgrade.sh --lock-only if the declared floors are what broke it." >&2
    exit 1
fi

KEEP=1
echo ""
echo "=============================="
echo "Upgraded"
echo "=============================="
echo "pyproject.toml and uv.lock are updated and the gate passes."

if [ "$LOCK_ONLY" -eq 0 ]; then
    echo ""
    echo "NOTE: AGENTS.md documents the runtime floors and says why each one is hard."
    echo "      Raised floors do not update that prose. Read it if a runtime floor moved."
fi
