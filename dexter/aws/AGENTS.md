# AGENTS.md — `dexter.aws`

Seven AWS clients, asynchronous over a synchronous SDK, with no boto3 type on any signature.

## Layout

**Every service is a package, and each holds a `client.py` plus whatever sub-concepts it earned.**
The uniformity is the point: a reader looking for how SQS batches, how SES assembles a request or
how DynamoDB serialises an item finds each in a file named after it, in the same place.

| Path | Holds |
| --- | --- |
| `session.py` | `AwsSession` — **the only file that constructs boto3**. Seven lazy clients |
| `_calling.py` | `call()` — the thread hop *and* the code→exception table. Every request goes through it |
| `_caching.py` | `TtlCache` — expiry plus one in-flight fetch per key. Shared by secrets and parameters |
| `values.py` | `ValueSource`, `StaticValue` — the provider pattern's contract and its local half |
| `errors.py` | The tree, rooted at `AwsError`. Shaped by what a caller does next, not by which service spoke |
| `models/` | `config`, `storage`, `messaging`, `items`, `conditions` — shape, never behaviour |
| `s3/` | `client`, `listing` (pagination), `presigning` (reaches no network) |
| `dynamodb/` | `client`, `conditions`↔`_expressions`, `paging`, `_items` (the type policy), `_batching` |
| `secrets/`, `parameters/` | `client` and `value` — *how it is fetched* and *where one value lives* |
| `sqs/` | `client`, `_batching` (chunking, entry ids), `_messages` (translation, guards) |
| `ses/` | `client`, `_request` (the four-level v2 shape, and the guards on building it) |
| `sns/` | `client`, `_envelopes` (attribute wrapping, the SMS-type table, the size limit) |
| `use.py` | `use_aws`, `register_aws_config`, `register_secret_value`, `register_parameter_value` |

Nothing but `client.py` is re-exported from a service package unless a consumer names it —
`SecretValue` and `ParameterValue` are, because a deployment binds them; `_batching`,
`_messages`, `_request` and `_envelopes` carry a leading underscore because nobody outside their
package should.

`tests/aws/test_boundaries.py` enforces the layout claims: which files may name boto3 — by path,
now that five files are called `client.py`, and checked in both directions so a stale allowance
fails — that no exported object is a boto3 type, and that nothing here imports another dexter
module.

## Decisions that are not obvious from the code

**The default executor caps concurrency at about thirty-two, not at `max_pool_connections`.**
`asyncio.to_thread` uses the loop's default `ThreadPoolExecutor`, sized `min(32, cpu_count + 4)`.
Anything holding a thread without using it spends that budget — one SQS receive with a
twenty-second long poll occupies a slot for twenty seconds, so a worker polling eight queues has
committed a quarter of the process. dexter drives no event loop and therefore sets no executor;
an application that fans out widely should set its own. This is documented rather than worked
around because the alternative is a library that quietly reconfigures its host.

**`signature_version` is S3's alone.** botocore applies `Config.signature_version` to whichever
service is being built without checking that the two agree, and `s3v4` resolves to `S3SigV4Auth`
— which skips URL-path normalisation and injects an S3-specific payload header. Handed to
DynamoDB or SQS it tends to verify anyway, which is what makes the mistake survivable and
therefore durable. `session.py` has two config methods for this reason and must keep them.

**Every client is built lazily.** Constructing one parses a service model and reads the machine's
shared AWS configuration, so a profile boto3 cannot use fails right there. Eagerly, that failure
would reach an application that was never going to call AWS at all.

**Absence is `None`; a missing container is an error.** `head_object` and `get_item` answer
`None`, and a queue with nothing on it answers an empty tuple. `ResourceNotFoundError` is for the
bucket, table, queue or topic — a missing object is an ordinary answer, and a missing bucket is a
deploy that never ran.

**`ConditionFailedError` is not an `AwsRequestError`.** Nothing about a lost conditional write is
wrong; the caller lost a race and must re-read rather than retry. `TransactionConflictError` is
its sibling precisely because that one *is* retryable. A transaction reports the reason under the
code `ConditionalCheckFailed` — without the `Exception` suffix the single-write failure carries,
which is a trap worth knowing before editing that translation.

**A batch never returns `None`.** S3, SQS and SNS all answer 200 with the refused entries in the
body, and DynamoDB answers 200 with an `UnprocessedItems` map. Reports and retries are what stop
those from being silent data loss under throttling — the moment it matters most.

**Nothing truncates.** `list_objects`, `query`, `scan` and `get_parameters_by_path` all paginate;
none takes a `max_keys` that silently caps. `query` and `scan` are the module's only non-`async`
public methods because they build a stream and reach no network — an `async def` returning a list
is the truncating version wearing a coroutine.

**`float` is refused on the way into DynamoDB.** Converting silently stores a number the caller
did not compute. `Decimal` survives; a whole number reads back as `int`.

## What is deliberately absent

| | |
| --- | --- |
| Table, queue, topic and bucket administration | Infrastructure is Terraform's. A framework that creates a table invites a schema defined twice |
| S3 multipart, streaming, cross-region clients | Presigned URLs are the large-object answer. A second region is a second `AwsSession` |
| Presigned-URL caching | A URL dies with the credentials that signed it, at a moment unrelated to its stated expiry |
| `put_parameter` | Writing configuration from the application is how a deployment stops being its repository |
| SES templates, identity management | `dexter.notification` renders nothing, and this must not contradict it |
| A recording double | `AwsConfig.endpoints` points real clients at a local stand-in, and `Stubber` validates every request against botocore's service model. A fake would accept `Buckett=` |
| Instrumentation | `dexter.observability` is Planned. `_calling.call()` is the one place it will go |

## Measured against

`itamoo-app`'s `backend/i/aws` is the reference this generalises, and the differences are the
value. It has no tests and no error translation, so `ClientError` reaches its handlers; it never
paginates a listing, so `list_objects` silently stops at a thousand keys; it drops
`UnprocessedItems` from every batch write; it dispatches its DynamoDB serializer on exact type,
so a `Decimal`, an `IntEnum` and a `frozenset` all raise, and reads numbers back through `float`;
it never passes `WithDecryption`, so a `SecureString` returns ciphertext that looks like a value;
it sets no SMS type, so a one-time code inherits the account's promotional default; and its
Secrets Manager provider fetches on every resolution, having written the TTL cache for SSM and
never applied it. Each of those is a named test here.
