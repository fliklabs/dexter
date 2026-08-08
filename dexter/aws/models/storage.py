"""What S3 answers with: one object described, a page of them, and what a bulk delete did.

Frozen pydantic throughout. These cross back *out* to a consumer, and validating on the way out
is what stops a change in a response shape from becoming a `KeyError` three layers away.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ObjectSummary(BaseModel):
    """What a HEAD, or one entry of a listing, says about a stored object.

    Deliberately not the object's body. This is what a caller asks for when the question is
    "is it there, and is it what I expected" — the case that matters when the upload was done
    by somebody else's browser through a presigned URL and the only proof it worked is that
    the object now exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    size_bytes: int
    content_type: str | None
    last_modified: datetime
    etag: str | None = None
    """The object's entity tag, when the answer carried one.

    A listing always gives it; a HEAD usually does. It is what a caller compares to decide
    whether the object changed — and for a single-part upload it is the MD5 of the body, which
    is why it is worth surfacing at all.
    """


class ObjectPage(BaseModel):
    """One response from a listing, before it is flattened into objects.

    Most callers want the objects and never this. It exists for the delimiter case, where the
    *prefixes* are the answer — listing "folders" under a path means reading `common_prefixes`
    and ignoring `objects` entirely.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    objects: tuple[ObjectSummary, ...] = ()
    common_prefixes: tuple[str, ...] = ()


class DeleteFailure(BaseModel):
    """One key a bulk delete could not remove, and what S3 said about it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    code: str
    message: str


class DeleteReport(BaseModel):
    """What a bulk delete actually did, key by key.

    **`DeleteObjects` is partially successful by design.** It answers HTTP 200 with a per-key
    `Errors` array inside the body, so a caller who checks only the status code has been told
    nothing. Returning `None` from a delete of a thousand keys — which is what the obvious
    signature does — silently discards the list of the ones that are still there.

    A caller who wants the strict behaviour writes `if report.failed: raise`, which is a line
    they can see rather than a guarantee they assumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: tuple[str, ...] = ()
    failed: tuple[DeleteFailure, ...] = ()
