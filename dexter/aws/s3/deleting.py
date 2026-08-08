"""Removing many objects at once, and reporting what actually happened.

**`DeleteObjects` is partially successful by design.** It answers HTTP 200 with the refused keys
listed in an `Errors` array inside the body, so a caller who checks only the status code has been
told nothing — and a signature returning `None` throws that array away before they could have.

Its own file because that answer needs assembling, and because the chunking has a rule worth
stating in one place: a thousand keys per request, sent one chunk after another rather than
concurrently. Forty chunks through `asyncio.gather` would be forty threads against an executor
that holds about thirty-two, which is a self-inflicted stall rather than parallelism.
"""

from collections.abc import Sequence
from typing import Any

from .._calling import call
from ..models import DeleteFailure, DeleteReport
from ..session import AwsSession

DELETE_BATCH_SIZE = 1000
"""How many keys one `DeleteObjects` may carry. A hard service limit, not a choice."""


async def delete_objects(
    session: AwsSession, bucket: str, keys: Sequence[str]
) -> DeleteReport:
    """Remove many objects, a thousand at a time.

    Returns:
        Which keys were removed and which were refused. **Check `failed`** — the request succeeds
        even when individual keys do not.

    Raises:
        AwsRequestError: If a request was refused or could not be made.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    deleted: list[str] = []
    failed: list[DeleteFailure] = []
    for start in range(0, len(keys), DELETE_BATCH_SIZE):
        chunk = keys[start : start + DELETE_BATCH_SIZE]
        response = await _delete_chunk(session, bucket, chunk)
        deleted.extend(entry["Key"] for entry in response.get("Deleted", []))
        failed.extend(
            DeleteFailure(
                key=entry.get("Key", ""),
                code=entry.get("Code", ""),
                message=entry.get("Message", ""),
            )
            for entry in response.get("Errors", [])
        )
    return DeleteReport(deleted=tuple(deleted), failed=tuple(failed))


async def _delete_chunk(
    session: AwsSession, bucket: str, keys: Sequence[str], /
) -> Any:
    """Delete one chunk of at most a thousand keys.

    Its own function rather than a lambda built inside the loop, because a closure over a loop
    variable is what ruff's B023 exists to catch.
    """
    return await call(
        f"DeleteObjects s3://{bucket}",
        lambda: session.s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in keys]},
        ),
    )
