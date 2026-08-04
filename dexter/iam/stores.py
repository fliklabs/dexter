"""Where outstanding magic codes are kept, when nowhere else will do.

One implementation, in memory. dexter ships no persistence — there is no database anywhere in
this library and adding one would be a decision about somebody else's deployment — so this is
what a consumer starts with and what the tests use.

**Be honest about what it is.** Codes live in a dictionary in one process. They do not survive
a restart, and two workers behind a load balancer each have their own, so a code issued by one
cannot be verified by the other. That is fine for a single-process service and for local
development, and it is wrong for anything horizontally scaled. Replacing it is one binding,
because `MagicCodeStore` is a `Protocol` and nothing here is imported by the service.
"""

from .models import MagicCode


class InMemoryMagicCodeStore:
    """A `MagicCodeStore` holding records in a dictionary.

    Bound `Scope.SINGLETON` by `use_in_memory_magic_codes`: it stands in for a database, and a
    database does not restart per request.

    **No lock, and none needed.** Every method completes without awaiting anything, so on one
    event loop no other task can interleave between a read and the write that follows it. A
    lock here would guard against a concurrency this code cannot experience.

    **Nothing is swept.** `MagicCodeService` deletes a record the moment it reads an expired
    one, so a key that is revisited cleans itself up; a key that is issued and then abandoned
    keeps a few hundred bytes until the process ends. A timer that reclaimed them would be a
    background task, and this library starts nothing on a consumer's behalf. A store backed by
    Redis or a TTL column gets the sweeping for free, which is another reason to move to one.
    """

    __slots__ = ("_codes",)

    def __init__(self) -> None:
        """Start with nothing outstanding."""
        self._codes: dict[str, MagicCode] = {}

    async def get(self, key: str) -> MagicCode | None:
        """The record for `key`, or `None` when there is none."""
        return self._codes.get(key)

    async def put(self, code: MagicCode) -> None:
        """Store `code`, replacing any record under the same key."""
        self._codes[code.key] = code

    async def delete(self, key: str) -> None:
        """Remove the record for `key`, if there is one."""
        self._codes.pop(key, None)

    def __len__(self) -> int:
        """How many records are outstanding. For tests and for a health endpoint."""
        return len(self._codes)

    def __repr__(self) -> str:
        return f"InMemoryMagicCodeStore(outstanding={len(self._codes)})"
