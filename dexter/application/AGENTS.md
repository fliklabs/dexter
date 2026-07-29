# dexter/application/AGENTS.md

Composing an application from modules. Read the source directly; this file records only the
decisions that are not visible in it.

## Layout

| File | Holds |
| --- | --- |
| `models.py` | The `Module` type and `describe_module` |
| `errors.py` | The exception tree |
| `registry.py` | `ModuleRegistry` — what an application is made of |
| `use.py` | `use_application` and `register_module` |

## This module depends on two others, and that is correct

`dexter.application` imports `dexter.cqrs` and `dexter.api`. Every other module depends only on
`commons` and the container, and the rule that keeps them independent — `dexter.api` must never
import `dexter.cqrs` — still holds. This one is the composition layer: being the single place
that knows about both is its entire job, and it is what lets a module contribute a command
handler and a route without either framework module hearing of the other.

Nothing here pulls in a web framework. `use_api` lives in `dexter.api`; only `dexter.api.http`
imports one.

## Why it exists at all

`use_cqrs` and `use_api` bind their registries unconditionally, and `ContainerBuilder.register`
refuses a repeat — so each may be called **exactly once per builder**. Before this module that
rule lived in prose in three docstrings, and the failure mode was a second module registering
itself and being told `CommandRegistry is already registered`, which names an internal type
rather than the mistake.

`use_application` calls them, once, ahead of every module. A module therefore *cannot* make
that mistake: it has nothing to call. `tests/application/test_wiring.py` pins both halves — that
`use_application` twice is refused, and that a module wiring the framework itself is too.

## The API registries are wired even for a worker

Deliberate, and the alternative is worse. A module declares everything it offers; an
application decides which of those surfaces to **expose**, not which to register. A second
topology omitting the API registries would mean a module contributing a route could not be
registered into it — so every module offering both a handler and a route would have to be split
in two, and the halves kept in step by hand for every application that exists. That split is
exactly what leaves a web application and a worker silently carrying different capabilities.

The cost is a few dictionary entries nobody reads. One list of modules, and the application
chooses what to do with the container.

## A module is a function, run immediately

Not a class, not a manifest collected now and executed later. Two consequences, both load-bearing:

- **A failure stays attached to its cause.** A malformed handler is raised inside the call to
  the module that declared it, with that module's frame on the traceback — rather than
  surfacing from a composition root flattening six lists, which has never heard of the module
  the entry came from.
- **The `use_*`-before-`register_*` ordering needs no enforcement.** Modules run when
  `register_module` is called, and that cannot happen before `use_application`.

## Module dependencies are not declared, and not checked

A module that needs what another provides asks the container for it by type. Nothing records
that, and nothing validates it: leaving a module out is reported when the dependency is
resolved, by `UnregisteredDependencyError` with the chain naming what was looked for and what
asked for it.

That is deliberately better than a declared dependency list, which would have to be kept true
by hand and would still not know whether the *binding* it names exists. It also makes
registration order irrelevant, which `tests/application/test_composition.py` pins both ways
round.

## Not implemented yet

Anything that would make `MODULES` conditional — environments, feature flags, a module that
registers only when another is present. A list a reader can follow is worth more than a
mechanism, and every conditional so far has been better expressed as a different list.
