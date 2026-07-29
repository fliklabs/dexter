# dexter/tools/AGENTS.md

Development-time tooling that ships. Read the source directly; this file records only the
decisions that are not visible in it.

## Why this is allowed to be here at all

Every other module in `dexter/` is application framework: it is composed into a container and
runs in production. Nothing in this package is ever imported by a running service, and that
makes it the exception — so it needs a reason, and it needs a rule.

**The reason:** the alternative is every repository built on dexter keeping its own copy of the
same file, and those copies drifting apart. A rewriter for `pyproject.toml` is precisely the
kind of thing that must not be duplicated per repository, because a bug in it damages the one
file every other tool reads.

**The rule: standard library only, always.** `AGENTS.md` is emphatic that consumers inherit
every declared dependency, and a module they never import still lands in their install. This
package is allowed to ship because it costs a consumer no dependency at all — only a few
kilobytes they never import. `tests/test_public_api.py` asserts that, and it is the line to
hold. The moment something here needs a third-party package, it belongs outside `dexter/`.

## Nothing is located from `__file__`

Every path is the caller's, defaulting to the working directory. Installed into a consumer's
environment, `__file__` points at their `site-packages`, so a tool that found the manifest that
way would rewrite the wrong project — or, far more likely, nothing at all, silently.

This is the difference between a script that happens to live in a package and a tool that ships.

## It is not wired into a container

There is no `use_tools`. None of this is a service: it is run from a shell during maintenance,
never resolved during a request. `dexter.commons` is the precedent for a module with no wiring.

## The orchestration deliberately stays out

`pins` understands a file format. Backing up, resolving, verifying, and keeping or reverting
stay in the repository that runs them, because **what "verify" means differs per project** —
dexter's own gate is `./verify.sh`, and a consumer's is whatever theirs is. `upgrade.sh` at the
root of this checkout is the worked example to copy, not an interface to depend on.

The split is also what keeps this package testable in the normal way. A bash script cannot be
covered by the suite; the part that can go wrong quietly is Python, and is.
