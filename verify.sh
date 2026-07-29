#!/usr/bin/env bash
#
# Single verification entry point. Local runs and CI run exactly these checks.
#
#   ./verify.sh          format check, lint, type-check, test with coverage
#   ./verify.sh --fix    format and autofix in place, then type-check and test
#   ./verify.sh -v       stream all output (CI uses this)
#
# The test step measures coverage and fails below the floor in pyproject.toml.
#
set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

FIX=0
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        --fix) FIX=1 ;;
        -v | --verbose) VERBOSE=1 ;;
        -h | --help)
            sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'
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

PASS=0
FAIL=0
CHECK_COUNTER=0

run_check() {
    local label="$1"
    shift

    if [ "$VERBOSE" -eq 1 ]; then
        echo ""
        echo "--- $label ---"
        if "$@"; then
            echo "PASS: $label"
            PASS=$((PASS + 1))
        else
            echo "FAIL: $label"
            FAIL=$((FAIL + 1))
        fi
        return
    fi

    CHECK_COUNTER=$((CHECK_COUNTER + 1))
    local output_file="${TMPDIR:-/tmp}/dexter-verify-$$-${CHECK_COUNTER}.log"
    if "$@" >"$output_file" 2>&1; then
        echo "PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $label"
        echo ""
        echo "--- output: $label ---"
        cat "$output_file" 2>/dev/null || true
        echo "---"
        FAIL=$((FAIL + 1))
    fi
    rm -f "$output_file"
}

# `uv run` is required rather than a bare `mypy`/`ruff`: mypy infers the Python version
# from the interpreter running it, so it must be the project interpreter. See AGENTS.md.
if [ "$FIX" -eq 1 ]; then
    run_check "format: ruff format" uv run ruff format .
    run_check "lint: ruff check --fix" uv run ruff check --fix .
else
    run_check "format: ruff format --check" uv run ruff format --check .
    run_check "lint: ruff check" uv run ruff check .
fi

run_check "types: mypy --strict" uv run mypy
run_check "tests: pytest" uv run pytest --cov --cov-report=term-missing

echo ""
echo "=============================="
echo "Results: $PASS passed, $FAIL failed"
echo "=============================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
