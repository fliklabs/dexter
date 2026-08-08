# AGENTS.md — `dexter.notification`

Sends messages without naming who sends them.

## Layout

| Path | Holds |
| --- | --- |
| `models.py` | `Email`, `EmailBody`, `EmailBodyType`, and the `EmailNotifier` protocol |
| `errors.py` | `NotificationError` and `DeliveryError`. That is the whole tree |
| `recording.py` | `RecordingEmailNotifier` — records, sends nothing |
| `use.py` | `use_recording_notification` |
| `resend/` | An engine — **the only directory that may import an HTTP client** |
| `ses/` | An engine — **the only directory that may import `dexter.aws`** |

Both boundaries are enforced by `tests/notification/test_boundaries.py`, by AST walk *and* by a
subprocess check that importing the core leaves `httpx` and `boto3` out. The first is what makes
`httpx` an optional extra (`dexter[resend]`) rather than a dependency every consumer inherits;
the second is what keeps the direction of the AWS dependency straight — `dexter.aws` names no
module here, and `ses/` is the one place the arrow points inward.

## Decisions that are not obvious from the code

**There is no bare `use_notification`.** The other modules have a topology switch because they
own a registry; this one owns none, so a `use_notification` that registered nothing would be
scenery. What it has instead is one `use_*` per engine, which is AGENTS.md's rule read
literally. Calling two binds `EmailNotifier` twice and the container refuses the second — the
right failure, because the alternative is an application that sends real mail from its tests.

**The two engines are asymmetric, and the asymmetry follows the configuration.** Resend needs an
API key, so it has a `ResendConfig` and a `register_resend_config`. SES needs a region and a
verified identity, and both belong to `dexter.aws` — so `use_ses_notification` takes nothing and
`use_aws` must have run. A `register_ses_config` would be this module inventing a second place to
write down what `AwsConfig` already says.

**Each `use_*` binds one object under both keys.** `EmailNotifier` for code that should name the
contract, and the concrete class for wiring or a test that deliberately names the engine. Two
`to(...)` bindings of one class produce two singletons, and then messages are recorded into one
and read from the other.

**The provider's own SDK is not used.** `resend` is synchronous and carries its API key in a
module-level global, so two containers in one process cannot use two accounts and the key is set
by whichever notifier sent first. Both are lost the moment it is imported, so a boundary test
forbids importing it. What is used instead is the one documented POST.

**A client per send.** dexter owns no connection pool and starts nothing in the background, so
there is no lifetime for a shared `httpx.AsyncClient` to belong to. A consumer wanting pooling
binds their own notifier over a client the container disposes.

**A plain-text body is sent as `text`, never as HTML.** itamoo wraps text in `<p>` unescaped, so
a `<` in a subject or a code renders as markup and the recipient sees something the sender never
wrote.

**Every answer is checked.** A non-2xx, a body that is not JSON, and a 200 that names no `id`
are each a `DeliveryError` saying what came back. itamoo indexes `response["id"]` unguarded.

## What is deliberately absent

| | |
| --- | --- |
| Templating | A subject and a body arrive composed. A framework that insists on its own engine is one consumers work around |
| Retries, queues, backoff | Delivery policy belongs to the application; `DeliveryError` is the seam |
| SMS, push | One contract per medium, added when there is a consumer, not in advance |
| A registry of engines | A second engine is a second `use_*`, not a row in a table |
