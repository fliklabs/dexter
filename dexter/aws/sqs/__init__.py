"""Queues: putting work on one, taking it off, and finishing with it.

**Long polling is on by default, at twenty seconds.** Short polling samples a subset of the
queue's servers, so it returns empty while messages are waiting, and a consumer looping on it
spends money on empty answers. The cost is the one thing to know before using this at scale:
every call here runs on `asyncio.to_thread`, whose executor holds about thirty-two slots, and a
twenty-second receive occupies one for the whole twenty seconds. A worker polling eight queues
with the default executor has committed a quarter of the process's capacity to waiting. dexter
drives no event loop and so sets no executor; an application doing this should set its own. See
`dexter/aws/AGENTS.md`.
"""

from .client import SqsClient as SqsClient
