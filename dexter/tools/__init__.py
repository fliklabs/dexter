"""Development-time tooling a project built on dexter can reuse.

**This is the one part of dexter that is not application framework.** Everything else here is
composed into a container and runs in production; nothing in this package is ever imported by a
running service. It ships anyway, because the alternative is every consuming repository keeping
its own copy of the same file and those copies drifting apart.

The cost is honest and small: this package is stdlib-only, so a consumer inherits no new
dependency from it — only a few kilobytes they never import. That is the whole reason it is
allowed to be here, and it is the rule to hold any future addition to.

It is deliberately *not* wired into a container. There is no `use_tools`, because none of this
is a service: it is run from a shell during maintenance, not resolved during a request.
`dexter.commons` is the precedent for a module with no wiring of its own.

**This re-exports nothing, which is deliberate** and the one place the usual convention is
inverted. Each tool here is runnable as `python -m dexter.tools.<name>`, and a package that
imports its own runnable submodule makes `runpy` execute that submodule twice — once on the
import, once as `__main__` — which warns on every single invocation and is an error under any
consumer running pytest with `filterwarnings = ["error"]`. `json` and `json.tool` are the
stdlib precedent for exactly this shape. Import what you want by name:

    from dexter.tools.pins import raise_floors
"""
