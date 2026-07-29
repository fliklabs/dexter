# dexter/api/AGENTS.md

Typed request handlers, served over HTTP today and other protocols later. Read the source
directly; this file records only the decisions that are not visible in it.

## Layout

| File | Holds |
| --- | --- |
| `models.py` | `ApiHandler`, `ApiMiddleware`, `ApiNext`, `Invocation`, `ErrorResponse` |
| `context.py` | `RequestContext`, `Headers`, `QueryValues`, `Cookie`, the `ContextVar` |
| `exposure.py` | `Exposure`, `HttpExposure`, `PayloadSource` — declarations, not machinery |
| `errors.py` | The exception tree. Imports from nothing in this module |
| `_introspection.py` | Private. Reads a handler's request and response types |
| `registry.py` | `ExposureRegistry` and `ErrorMap` |
| `pipeline.py` | `ApiPipeline` — ordered composition around one request |
| `use.py` | `use_api` and the three `register_*` functions |
| `http/` | The adapter. **The only directory that may import a web framework** |

`context.py` holds the value types rather than `models.py` because `Invocation` names
`RequestContext`; the other arrangement is an import cycle.

## Two import rules, and a test for each

1. **Only `dexter/api/http/**` may import `fastapi` or `starlette`.**
2. **Nothing under `dexter/api/` may import another dexter module** beyond `commons` and
   `dependency_injection`. In particular it never imports `dexter.cqrs`.

`tests/api/test_boundaries.py` walks the package's syntax tree and asserts both, and
`tests/test_public_api.py` asserts in a subprocess that `import dexter.api` leaves
`fastapi` out of `sys.modules`. The negative check alone would pass if the adapter were simply
broken, so the positive one is there too — the same pairing that guards `dexter.cli` against
`curses`.

**`create_app` is therefore not re-exported from `dexter.api`.** It is
`from dexter.api.http import create_app`. That is the seam written into the import path, and
it is what makes rule 1 enforceable at the package level rather than file by file.

## The context is injected, not passed

`handle` takes **one** argument, exactly as a CQRS handler does. Everything else about the
invocation arrives through a `Scope.SCOPED` `RequestContext` resolved from the container.

The reason is not the handler. It is that the thing wanting the caller's identity is usually
something further down — a repository wanting the tenant, an audit service wanting the address.
A second `handle` parameter fixes the handler and leaves every one of those threading the value
by hand, which is the pressure that produces an ambient global. A container binding is reachable
from any depth by declaring a parameter.

`dexter/cqrs/AGENTS.md` records the same call for the same reason: passing the envelope to
`handle` was rejected because it would put dexter in every handler signature and every handler
test.

**The ambient half is a `ContextVar` and must stay one.** A `threading.local` is shared by every
coroutine on the event loop's thread, so under any concurrency it hands one request's caller to
another. `tests/api/test_context.py::TestIsolation` fails on a thread-local and passes on a
`ContextVar`; deleting it loses the guarantee silently.

Three guards make "no request" loud, and two are free: `Scope.SCOPED` means resolving from the
root raises `ScopeRequiredError`; `build()` rejects any singleton that reaches it with
`CaptiveDependencyError`; and `current_request()` raises `NoRequestContextError`.

## The scope opens and closes inside the endpoint

Not in a framework dependency, and not in ASGI middleware. The ordering that matters is that
the scope closes — running every `dispose=` — **before** the response is produced, so an
application that also wired `dexter.cqrs` has settled its buses before the caller is told
anything, and a `DisposalError` is still ours to map.

A dependency's teardown runs outside the endpoint's `try`, so a settling failure would bypass
the error map. ASGI middleware runs before routing, so it would open a scope for every 404 and
could not know which handler was about to run.

**Streaming is consequently unsupported**, and declining it is deliberate: a streaming body is
produced after the endpoint returns, when the scope is already closed.

## `Invocation.handler` is the class, not an instance

So a middleware that refuses a request never constructs the handler or anything it depends on —
which for a rejected caller might otherwise mean opening a database connection. The handler is
resolved in the pipeline's terminal, and nowhere else.

## Endpoint construction: one rule, two branches

**The path names the `Path()` parameters; every remaining field becomes one payload parameter.**

- No path parameters → the payload parameter *is* the request model. The generated schema is
  named after it and every validator on it, `@model_validator` included, runs inside the
  framework's own validation.
- Path parameters present → the remainder is copied into a model derived with `create_model`,
  each field carried across as an `(annotation, FieldInfo)` pair. That pair is what transports
  constraints, descriptions, defaults and aliases into the schema — **do not reconstruct a
  field attribute by attribute**, which drops whatever the author forgot.

Both branches rebuild the real request model afterwards, so a rule declared on the whole model
applies either way. `tests/api/test_routing.py` pins that with a `model_post_init` check.

The signature is presented by assigning `__signature__`; the framework reads endpoint
parameters with `inspect.signature`, which honours it, and never reads source. No
code-generation dependency is needed, and none should be added.

## Error mapping walks the MRO

Deliberately unlike `dexter.cqrs`, whose handler lookup is on the exact runtime class. An
exception hierarchy exists in order to be caught by base class — that is what `except` does —
so registering a base has to cover its subclasses. The usual objection, that the winner depends
on MRO order, does not bite: MRO order is what `except` already resolves in.

`ErrorMap` ships **empty**. An unmapped exception is re-raised, not tidied into a 500 here: the
framework's own handling, the consumer's exception handlers and every logging integration
depend on seeing it. The framework's own `HTTPException` and `RequestValidationError` are
checked for explicitly and never mapped, so a consumer mapping something broad cannot swallow
them.

## Names carry an `Api` prefix where `dexter.cqrs` owns the plain one

`ApiMiddleware`, `ApiNext`, `ApiPipeline`, `register_api_middleware`. An application wiring both
modules imports them into one file — `examples/frontdesk/wiring.py` does — and
`dexter/cli/AGENTS.md` records the same trap for `CommandTree` versus `CommandRegistry`.
`register_handler` needs no prefix because nothing collides with it.

## Not implemented yet

GraphQL and websocket exposures. The seam for them is `Exposure` plus
`ExposureRegistry.of(kind)`, and it is the whole of it: a second protocol is a subclass, a
package beside `http/`, and a reader that asks the registry for its own kind. Nothing in
`use.py`, `registry.py`, `pipeline.py` or any handler changes. Deliberately absent until then:
a transport enum, an adapter base class, and per-protocol registries.

Also absent: a response for a handler that wants to stream, and any way to bind a request model
field to a header. The second is a temptation worth naming — it reads well and it makes the
request model transport-aware, which breaks the seam at its most load-bearing point. Headers
belong on the context.
