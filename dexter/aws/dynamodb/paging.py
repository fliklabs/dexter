"""Walking the results of a query or a scan.

**A page that holds no items is not the end**, and that is the whole reason this file is not four
lines. DynamoDB applies a filter *after* reading, and stops reading once it has examined a
megabyte — so a filtered query routinely answers with an empty `Items` and a `LastEvaluatedKey`
saying there is more. Every client that treats an empty page as the end silently returns
nothing for a query that has results, and it does it only when the data is large enough, which
is to say in production.

The end of the results is `LastEvaluatedKey` being absent. That is the only signal.

Like `ObjectStream`, this is returned by a **synchronous** method: it holds a query and performs
no I/O until it is iterated, so there is nothing to await when it is built.
"""

from collections.abc import AsyncIterator
from typing import Any

from .._calling import call
from ..models import Item, ItemPage
from ..session import AwsSession
from ._items import deserialise


class ItemStream:
    """Every item a query or scan matches, fetched a page at a time as it is consumed.

    Iterate it for items, or call `pages()` when the page boundaries matter — which they do for
    a caller that wants to checkpoint, since `ItemPage.last_key` is exactly what a later run
    would resume from.

    **Re-iterating starts again from the first page.** It holds a query, not a result.
    """

    __slots__ = ("_operation", "_request", "_session", "_table")

    def __init__(
        self,
        session: AwsSession,
        table: str,
        operation: str,
        request: dict[str, Any],
    ) -> None:
        """Record the request. Nothing is sent until iteration begins.

        Args:
            session: The boto3 clients to read through.
            table: The table being read, for the error message.
            operation: `"Query"` or `"Scan"`.
            request: The fully built request, minus the pagination key.
        """
        self._session = session
        self._table = table
        self._operation = operation
        self._request = request

    async def __aiter__(self) -> AsyncIterator[Item]:
        """Every matching item, in the order DynamoDB returns them.

        Raises:
            ResourceNotFoundError: If the table does not exist.
            AwsRequestError: If a read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        async for page in self.pages():
            for item in page.items:
                yield item

    async def pages(self) -> AsyncIterator[ItemPage]:
        """Each response in turn, with the key the next one would start from."""
        start: dict[str, Any] | None = None
        while True:
            request = dict(self._request)
            if start is not None:
                request["ExclusiveStartKey"] = start

            response = await self._page(request)
            last = response.get("LastEvaluatedKey")
            yield ItemPage(
                items=tuple(deserialise(item) for item in response.get("Items", [])),
                last_key=deserialise(last) if last else None,
            )

            if not last:
                return
            start = last

    async def _page(self, request: dict[str, Any], /) -> Any:
        """One page of the query or scan.

        Its own method rather than a lambda built inside the loop, because a closure over a loop
        variable is what ruff's B023 exists to catch.
        """
        if self._operation == "Query":
            return await call(
                f"Query {self._table}",
                lambda: self._session.dynamodb.query(**request),
            )
        return await call(
            f"Scan {self._table}",
            lambda: self._session.dynamodb.scan(**request),
        )
