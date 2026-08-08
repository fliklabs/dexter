"""Covers `dexter.aws.dynamodb.client` and `paging`: the ten operations and what they return.

Three groups carry the weight, and each guards a failure that is silent without a test:

- **Paging.** A filtered query answers an empty page with a `LastEvaluatedKey`, so stopping on an
  empty page returns nothing for a query that has results.
- **Batching.** DynamoDB answers 200 with an `UnprocessedItems` map, so a client that ignores it
  loses writes under throttling.
- **Conditions.** A lost conditional write is not a bad request and must not be retried the same
  way a conflict should be.
"""

from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from dexter.aws import (
    AccessDeniedError,
    Attr,
    AwsRequestError,
    AwsSession,
    BatchIncompleteError,
    ConditionFailedError,
    DeleteRequest,
    DynamoDbClient,
    Key,
    PutRequest,
    ResourceNotFoundError,
    ThrottledError,
    TransactConditionCheck,
    TransactDelete,
    TransactGet,
    TransactionConflictError,
    TransactPut,
    TransactUpdate,
)
from dexter.aws.dynamodb._failures import translate_cancellation

TABLE = "orders"
ITEM = {"pk": "u#1", "sk": "order#1", "total": Decimal("19.99")}
STORED = {"pk": {"S": "u#1"}, "sk": {"S": "order#1"}, "total": {"N": "19.99"}}
ITEM_KEY = {"pk": "u#1", "sk": "order#1"}
STORED_KEY = {"pk": {"S": "u#1"}, "sk": {"S": "order#1"}}


def cancelled(reasons: list[dict[str, Any]], /) -> dict[str, Any]:
    """The response body of a cancelled transaction."""
    return {
        "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
        "CancellationReasons": reasons,
    }


class TestGetItem:
    async def test_returns_the_item(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "get_item",
            {"Item": STORED},
            {"TableName": TABLE, "Key": STORED_KEY, "ConsistentRead": False},
        )
        assert await DynamoDbClient(session).get_item(TABLE, ITEM_KEY) == ITEM

    async def test_answers_none_when_there_is_nothing_there(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """**Absence is an ordinary answer**, which is why there is no `ItemNotFoundError`."""
        dynamodb_stub.add_response(
            "get_item",
            {},
            {"TableName": TABLE, "Key": STORED_KEY, "ConsistentRead": False},
        )
        assert await DynamoDbClient(session).get_item(TABLE, ITEM_KEY) is None

    async def test_a_consistent_read_says_so(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "get_item",
            {"Item": STORED},
            {"TableName": TABLE, "Key": STORED_KEY, "ConsistentRead": True},
        )
        await DynamoDbClient(session).get_item(TABLE, ITEM_KEY, consistent_read=True)

    async def test_a_projection_escapes_every_name(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "get_item",
            {"Item": STORED},
            {
                "TableName": TABLE,
                "Key": STORED_KEY,
                "ConsistentRead": False,
                "ProjectionExpression": "#p0, #p1",
                "ExpressionAttributeNames": {"#p0": "pk", "#p1": "total"},
            },
        )
        await DynamoDbClient(session).get_item(
            TABLE, ITEM_KEY, attributes=["pk", "total"]
        )


class TestPutItem:
    async def test_writes_the_item(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response("put_item", {}, {"TableName": TABLE, "Item": STORED})
        await DynamoDbClient(session).put_item(TABLE, ITEM)

    async def test_a_condition_travels_with_it(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "put_item",
            {},
            {
                "TableName": TABLE,
                "Item": STORED,
                "ConditionExpression": "attribute_not_exists(#n0)",
                "ExpressionAttributeNames": {"#n0": "pk"},
            },
        )
        await DynamoDbClient(session).put_item(
            TABLE, ITEM, condition=Attr("pk").not_exists()
        )

    async def test_a_lost_condition_is_not_a_request_error(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """**The distinction optimistic concurrency depends on.**

        Nothing about the request was wrong; the caller lost a race. Retrying the identical
        request is guaranteed to fail again, so this must not be caught as a transient failure.
        """
        dynamodb_stub.add_client_error(
            "put_item",
            service_error_code="ConditionalCheckFailedException",
            http_status_code=400,
        )
        with pytest.raises(ConditionFailedError):
            await DynamoDbClient(session).put_item(
                TABLE, ITEM, condition=Attr("pk").not_exists()
            )

    async def test_a_throttle_is_still_a_throttle(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_client_error(
            "put_item",
            service_error_code="ProvisionedThroughputExceededException",
            http_status_code=400,
        )
        with pytest.raises(ThrottledError):
            await DynamoDbClient(session).put_item(TABLE, ITEM)


class TestUpdateItem:
    async def test_sets_an_attribute(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "update_item",
            {},
            {
                "TableName": TABLE,
                "Key": STORED_KEY,
                "UpdateExpression": "SET #u0 = :u0",
                "ReturnValues": "NONE",
                "ExpressionAttributeNames": {"#u0": "status"},
                "ExpressionAttributeValues": {":u0": {"S": "PAID"}},
            },
        )
        assert (
            await DynamoDbClient(session).update_item(
                TABLE, ITEM_KEY, set_values={"status": "PAID"}
            )
            is None
        )

    async def test_returns_the_updated_item_when_asked(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "update_item",
            {"Attributes": STORED},
            {
                "TableName": TABLE,
                "Key": STORED_KEY,
                "UpdateExpression": "ADD #u0 :u0",
                "ReturnValues": "ALL_NEW",
                "ExpressionAttributeNames": {"#u0": "views"},
                "ExpressionAttributeValues": {":u0": {"N": "1"}},
            },
        )
        updated = await DynamoDbClient(session).update_item(
            TABLE, ITEM_KEY, add={"views": 1}, return_updated=True
        )
        assert updated == ITEM

    async def test_an_update_and_a_condition_share_one_request(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """Both placeholder maps travel together, and the prefixes keep them apart."""
        dynamodb_stub.add_response(
            "update_item",
            {},
            {
                "TableName": TABLE,
                "Key": STORED_KEY,
                "UpdateExpression": "SET #u0 = :u0",
                "ConditionExpression": "#n0 = :v0",
                "ReturnValues": "NONE",
                "ExpressionAttributeNames": {"#u0": "status", "#n0": "version"},
                "ExpressionAttributeValues": {
                    ":u0": {"S": "PAID"},
                    ":v0": {"N": "3"},
                },
            },
        )
        await DynamoDbClient(session).update_item(
            TABLE,
            ITEM_KEY,
            set_values={"status": "PAID"},
            condition=Attr("version").equals(3),
        )

    async def test_an_update_that_changes_nothing_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        with pytest.raises(ValueError, match="must change something"):
            await DynamoDbClient(session).update_item(TABLE, ITEM_KEY)


class TestDeleteItem:
    async def test_removes_the_item(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "delete_item", {}, {"TableName": TABLE, "Key": STORED_KEY}
        )
        await DynamoDbClient(session).delete_item(TABLE, ITEM_KEY)

    async def test_a_lost_condition_is_reported(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_client_error(
            "delete_item",
            service_error_code="ConditionalCheckFailedException",
            http_status_code=400,
        )
        with pytest.raises(ConditionFailedError, match="delete"):
            await DynamoDbClient(session).delete_item(
                TABLE, ITEM_KEY, condition=Attr("status").equals("DRAFT")
            )


class TestQuery:
    async def test_returns_matching_items(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "query",
            {"Items": [STORED]},
            {
                "TableName": TABLE,
                "KeyConditionExpression": "#n0 = :v0",
                "ScanIndexForward": True,
                "ConsistentRead": False,
                "Limit": 100,
                "ExpressionAttributeNames": {"#n0": "pk"},
                "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
            },
        )
        stream = DynamoDbClient(session).query(TABLE, Key("pk").equals("u#1"))

        assert [item async for item in stream] == [ITEM]

    async def test_building_a_query_reaches_no_network(
        self, session: AwsSession
    ) -> None:
        """**Why `query` is not `async`.** No stub is installed, so a request would fail."""
        assert DynamoDbClient(session).query(TABLE, Key("pk").equals("u#1")) is not None

    async def test_a_filter_and_an_index_travel_with_it(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "query",
            {"Items": []},
            {
                "TableName": TABLE,
                "IndexName": "by-status",
                "KeyConditionExpression": "#n0 = :v0",
                "FilterExpression": "#n1 <> :v1",
                "ScanIndexForward": False,
                "ConsistentRead": False,
                "Limit": 100,
                "ExpressionAttributeNames": {"#n0": "pk", "#n1": "status"},
                "ExpressionAttributeValues": {
                    ":v0": {"S": "u#1"},
                    ":v1": {"S": "CANCELLED"},
                },
            },
        )
        stream = DynamoDbClient(session).query(
            TABLE,
            Key("pk").equals("u#1"),
            index="by-status",
            filter=Attr("status").not_equals("CANCELLED"),
            ascending=False,
        )
        assert [item async for item in stream] == []


class TestPaging:
    async def test_follows_the_last_evaluated_key(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        expected = {
            "TableName": TABLE,
            "KeyConditionExpression": "#n0 = :v0",
            "ScanIndexForward": True,
            "ConsistentRead": False,
            "Limit": 100,
            "ExpressionAttributeNames": {"#n0": "pk"},
            "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
        }
        dynamodb_stub.add_response(
            "query", {"Items": [STORED], "LastEvaluatedKey": STORED_KEY}, expected
        )
        dynamodb_stub.add_response(
            "query", {"Items": [STORED]}, {**expected, "ExclusiveStartKey": STORED_KEY}
        )
        stream = DynamoDbClient(session).query(TABLE, Key("pk").equals("u#1"))

        assert len([item async for item in stream]) == 2

    async def test_an_empty_page_with_a_last_key_is_not_the_end(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """**The DynamoDB trap that catches everyone.**

        A filter is applied after the read, and the read stops at a megabyte — so a page can be
        entirely filtered out while more results remain. Stopping on an empty page returns
        nothing for a query that has answers, and only once the data is large enough.
        """
        expected = {
            "TableName": TABLE,
            "KeyConditionExpression": "#n0 = :v0",
            "ScanIndexForward": True,
            "ConsistentRead": False,
            "Limit": 100,
            "ExpressionAttributeNames": {"#n0": "pk"},
            "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
        }
        dynamodb_stub.add_response(
            "query", {"Items": [], "LastEvaluatedKey": STORED_KEY}, expected
        )
        dynamodb_stub.add_response(
            "query", {"Items": [STORED]}, {**expected, "ExclusiveStartKey": STORED_KEY}
        )
        stream = DynamoDbClient(session).query(TABLE, Key("pk").equals("u#1"))

        assert [item async for item in stream] == [ITEM]

    async def test_pages_carry_the_key_to_resume_from(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """What a caller checkpoints, and it comes back as ordinary Python values."""
        dynamodb_stub.add_response(
            "query",
            {"Items": [STORED], "LastEvaluatedKey": STORED_KEY},
            {
                "TableName": TABLE,
                "KeyConditionExpression": "#n0 = :v0",
                "ScanIndexForward": True,
                "ConsistentRead": False,
                "Limit": 100,
                "ExpressionAttributeNames": {"#n0": "pk"},
                "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
            },
        )
        dynamodb_stub.add_response(
            "query",
            {"Items": []},
            {
                "TableName": TABLE,
                "KeyConditionExpression": "#n0 = :v0",
                "ScanIndexForward": True,
                "ConsistentRead": False,
                "Limit": 100,
                "ExclusiveStartKey": STORED_KEY,
                "ExpressionAttributeNames": {"#n0": "pk"},
                "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
            },
        )
        stream = DynamoDbClient(session).query(TABLE, Key("pk").equals("u#1"))

        pages = [page async for page in stream.pages()]
        assert pages[0].last_key == ITEM_KEY
        assert pages[1].last_key is None


class TestScan:
    async def test_reads_the_whole_table(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "scan", {"Items": [STORED]}, {"TableName": TABLE, "Limit": 100}
        )
        stream = DynamoDbClient(session).scan(TABLE)

        assert [item async for item in stream] == [ITEM]

    async def test_a_parallel_scan_names_its_segment(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "scan",
            {"Items": []},
            {"TableName": TABLE, "Limit": 100, "Segment": 1, "TotalSegments": 4},
        )
        stream = DynamoDbClient(session).scan(TABLE, segment=1, total_segments=4)
        assert [item async for item in stream] == []


class TestBatchWrite:
    async def test_writes_and_deletes_together(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "batch_write_item",
            {},
            {
                "RequestItems": {
                    TABLE: [
                        {"PutRequest": {"Item": STORED}},
                        {"DeleteRequest": {"Key": STORED_KEY}},
                    ]
                }
            },
        )
        await DynamoDbClient(session).batch_write_items(
            {TABLE: [PutRequest(ITEM), DeleteRequest(ITEM_KEY)]}
        )

    async def test_chunks_at_twenty_five(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        writes = [PutRequest({"pk": f"u#{index}"}) for index in range(26)]
        for chunk in (range(25), range(25, 26)):
            dynamodb_stub.add_response(
                "batch_write_item",
                {},
                {
                    "RequestItems": {
                        TABLE: [
                            {"PutRequest": {"Item": {"pk": {"S": f"u#{index}"}}}}
                            for index in chunk
                        ]
                    }
                },
            )
        await DynamoDbClient(session).batch_write_items({TABLE: writes})

    async def test_retries_what_the_service_declined(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """**The silent data loss this exists to prevent.**

        DynamoDB answers 200 with an `UnprocessedItems` map when it throttles. A client that
        reads only the status code has lost those writes, and it loses them exactly when
        throughput is short.
        """
        entry = {"PutRequest": {"Item": STORED}}
        dynamodb_stub.add_response(
            "batch_write_item",
            {"UnprocessedItems": {TABLE: [entry]}},
            {"RequestItems": {TABLE: [entry]}},
        )
        dynamodb_stub.add_response(
            "batch_write_item", {}, {"RequestItems": {TABLE: [entry]}}
        )

        await DynamoDbClient(session).batch_write_items({TABLE: [PutRequest(ITEM)]})

    async def test_gives_up_loudly_when_the_budget_runs_out(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        entry = {"PutRequest": {"Item": STORED}}
        for _ in range(session.config.max_attempts):
            dynamodb_stub.add_response(
                "batch_write_item",
                {"UnprocessedItems": {TABLE: [entry]}},
                {"RequestItems": {TABLE: [entry]}},
            )

        with pytest.raises(BatchIncompleteError, match="1 entries"):
            await DynamoDbClient(session).batch_write_items({TABLE: [PutRequest(ITEM)]})


class TestBatchGet:
    async def test_returns_the_items_found(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "batch_get_item",
            {"Responses": {TABLE: [STORED]}},
            {"RequestItems": {TABLE: {"Keys": [STORED_KEY], "ConsistentRead": False}}},
        )
        found = await DynamoDbClient(session).batch_get_items({TABLE: [ITEM_KEY]})
        assert found == {TABLE: [ITEM]}

    async def test_a_missing_key_is_simply_absent(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """DynamoDB's own answer, rather than a failure: the result may be shorter."""
        dynamodb_stub.add_response(
            "batch_get_item",
            {"Responses": {TABLE: []}},
            {"RequestItems": {TABLE: {"Keys": [STORED_KEY], "ConsistentRead": False}}},
        )
        assert await DynamoDbClient(session).batch_get_items({TABLE: [ITEM_KEY]}) == {
            TABLE: []
        }

    async def test_retries_unprocessed_keys(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """Same shape as a batch write, and the same silent partial answer without it."""
        pending = {TABLE: {"Keys": [STORED_KEY], "ConsistentRead": False}}
        dynamodb_stub.add_response(
            "batch_get_item",
            {"Responses": {TABLE: []}, "UnprocessedKeys": pending},
            {"RequestItems": pending},
        )
        dynamodb_stub.add_response(
            "batch_get_item",
            {"Responses": {TABLE: [STORED]}},
            {"RequestItems": pending},
        )

        found = await DynamoDbClient(session).batch_get_items({TABLE: [ITEM_KEY]})
        assert found == {TABLE: [ITEM]}


class TestTransactions:
    async def test_writes_several_kinds_of_entry(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [
                    {"Put": {"TableName": TABLE, "Item": STORED}},
                    {"Delete": {"TableName": TABLE, "Key": STORED_KEY}},
                    {
                        "ConditionCheck": {
                            "TableName": TABLE,
                            "Key": STORED_KEY,
                            "ConditionExpression": "attribute_exists(#n0)",
                            "ExpressionAttributeNames": {"#n0": "pk"},
                        }
                    },
                ]
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [
                TransactPut(TABLE, ITEM),
                TransactDelete(TABLE, ITEM_KEY),
                TransactConditionCheck(TABLE, ITEM_KEY, Attr("pk").exists()),
            ]
        )

    async def test_an_update_entry_compiles_its_expression(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [
                    {
                        "Update": {
                            "TableName": TABLE,
                            "Key": STORED_KEY,
                            "UpdateExpression": "SET #u0 = :u0",
                            "ExpressionAttributeNames": {"#u0": "status"},
                            "ExpressionAttributeValues": {":u0": {"S": "PAID"}},
                        }
                    }
                ]
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [TransactUpdate(TABLE, ITEM_KEY, set_values={"status": "PAID"})]
        )

    async def test_an_empty_transaction_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        with pytest.raises(ValueError, match="at least one"):
            await DynamoDbClient(session).transact_write_items([])

    async def test_an_oversized_transaction_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            await DynamoDbClient(session).transact_write_items(
                [TransactPut(TABLE, ITEM)] * 101
            )

    async def test_a_request_token_travels_when_given(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """Never invented here: one dexter generated would differ on the caller's own retry,
        which is exactly when idempotency is supposed to help."""
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [{"Put": {"TableName": TABLE, "Item": STORED}}],
                "ClientRequestToken": "abc",
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [TransactPut(TABLE, ITEM)], request_token="abc"
        )


class TestTransactionalReads:
    async def test_returns_one_entry_per_request_in_order(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_get_items",
            {"Responses": [{"Item": STORED}, {}]},
            {
                "TransactItems": [
                    {"Get": {"TableName": TABLE, "Key": STORED_KEY}},
                    {"Get": {"TableName": TABLE, "Key": STORED_KEY}},
                ]
            },
        )
        found = await DynamoDbClient(session).transact_get_items(
            [TransactGet(TABLE, ITEM_KEY), TransactGet(TABLE, ITEM_KEY)]
        )
        assert found == [ITEM, None]

    async def test_an_empty_read_is_refused_locally(self, session: AwsSession) -> None:
        with pytest.raises(ValueError, match="at least one"):
            await DynamoDbClient(session).transact_get_items([])


class TestCancellationReasons:
    def test_a_failed_condition_becomes_its_own_error(self) -> None:
        """Driven directly: inducing a real cancellation needs a real transaction, and the
        mapping is what matters rather than the route to it."""
        error = ClientError(
            cancelled([{"Code": "None"}, {"Code": "ConditionalCheckFailed"}]),  # type: ignore[arg-type]
            "TransactWriteItems",
        )
        with pytest.raises(ConditionFailedError, match="entry 1"):
            translate_cancellation(error)

    def test_a_conflict_becomes_a_retryable_error(self) -> None:
        """Two classes because one is retryable and the other never is."""
        error = ClientError(
            cancelled([{"Code": "TransactionConflict"}]),  # type: ignore[arg-type]
            "TransactWriteItems",
        )
        with pytest.raises(TransactionConflictError, match="entry 0"):
            translate_cancellation(error)

    def test_reasons_naming_nothing_are_left_alone(self) -> None:
        """So the caller sees the general translation rather than a guess."""
        error = ClientError(
            cancelled([{"Code": "None"}]),  # type: ignore[arg-type]
            "TransactWriteItems",
        )
        translate_cancellation(error)


class TestMoreCoverage:
    async def test_a_transactional_update_carries_its_condition(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [
                    {
                        "Update": {
                            "TableName": TABLE,
                            "Key": STORED_KEY,
                            "UpdateExpression": "SET #u0 = :u0",
                            "ConditionExpression": "#n0 = :v0",
                            "ExpressionAttributeNames": {
                                "#u0": "status",
                                "#n0": "version",
                            },
                            "ExpressionAttributeValues": {
                                ":u0": {"S": "PAID"},
                                ":v0": {"N": "3"},
                            },
                        }
                    }
                ]
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [
                TransactUpdate(
                    TABLE,
                    ITEM_KEY,
                    set_values={"status": "PAID"},
                    condition=Attr("version").equals(3),
                )
            ]
        )

    async def test_a_transactional_read_can_project(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_get_items",
            {"Responses": [{"Item": STORED}]},
            {
                "TransactItems": [
                    {
                        "Get": {
                            "TableName": TABLE,
                            "Key": STORED_KEY,
                            "ProjectionExpression": "#p0",
                            "ExpressionAttributeNames": {"#p0": "total"},
                        }
                    }
                ]
            },
        )
        await DynamoDbClient(session).transact_get_items(
            [TransactGet(TABLE, ITEM_KEY, attributes=("total",))]
        )

    async def test_a_transactional_put_carries_its_condition(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [
                    {
                        "Put": {
                            "TableName": TABLE,
                            "Item": STORED,
                            "ConditionExpression": "attribute_not_exists(#n0)",
                            "ExpressionAttributeNames": {"#n0": "pk"},
                        }
                    }
                ]
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [TransactPut(TABLE, ITEM, condition=Attr("pk").not_exists())]
        )

    async def test_a_transactional_delete_carries_its_condition(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "transact_write_items",
            {},
            {
                "TransactItems": [
                    {
                        "Delete": {
                            "TableName": TABLE,
                            "Key": STORED_KEY,
                            "ConditionExpression": "#n0 = :v0",
                            "ExpressionAttributeNames": {"#n0": "status"},
                            "ExpressionAttributeValues": {":v0": {"S": "DRAFT"}},
                        }
                    }
                ]
            },
        )
        await DynamoDbClient(session).transact_write_items(
            [TransactDelete(TABLE, ITEM_KEY, condition=Attr("status").equals("DRAFT"))]
        )

    async def test_a_batch_read_chunks_at_a_hundred_keys(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        keys = [{"pk": f"u#{index}"} for index in range(101)]
        for chunk in (range(100), range(100, 101)):
            dynamodb_stub.add_response(
                "batch_get_item",
                {"Responses": {TABLE: []}},
                {
                    "RequestItems": {
                        TABLE: {
                            "Keys": [{"pk": {"S": f"u#{index}"}} for index in chunk],
                            "ConsistentRead": False,
                        }
                    }
                },
            )
        assert await DynamoDbClient(session).batch_get_items({TABLE: keys}) == {
            TABLE: []
        }

    async def test_a_batch_read_gives_up_loudly(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        pending = {TABLE: {"Keys": [STORED_KEY], "ConsistentRead": False}}
        for _ in range(session.config.max_attempts):
            dynamodb_stub.add_response(
                "batch_get_item",
                {"Responses": {TABLE: []}, "UnprocessedKeys": pending},
                {"RequestItems": pending},
            )

        with pytest.raises(BatchIncompleteError, match="1 keys"):
            await DynamoDbClient(session).batch_get_items({TABLE: [ITEM_KEY]})

    async def test_a_scan_carries_a_filter_and_a_projection(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "scan",
            {"Items": []},
            {
                "TableName": TABLE,
                "Limit": 100,
                "IndexName": "by-status",
                "FilterExpression": "#n0 = :v0",
                "ProjectionExpression": "#p0",
                "ExpressionAttributeNames": {"#n0": "status", "#p0": "pk"},
                "ExpressionAttributeValues": {":v0": {"S": "PAID"}},
            },
        )
        stream = DynamoDbClient(session).scan(
            TABLE,
            index="by-status",
            filter=Attr("status").equals("PAID"),
            attributes=["pk"],
        )
        assert [item async for item in stream] == []

    async def test_a_query_can_project_and_read_consistently(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_response(
            "query",
            {"Items": []},
            {
                "TableName": TABLE,
                "KeyConditionExpression": "#n0 = :v0",
                "ScanIndexForward": True,
                "ConsistentRead": True,
                "Limit": 10,
                "ProjectionExpression": "#p0",
                "ExpressionAttributeNames": {"#n0": "pk", "#p0": "total"},
                "ExpressionAttributeValues": {":v0": {"S": "u#1"}},
            },
        )
        stream = DynamoDbClient(session).query(
            TABLE,
            Key("pk").equals("u#1"),
            attributes=["total"],
            consistent_read=True,
            page_size=10,
        )
        assert [item async for item in stream] == []


class TestFailuresThatAreNotConditions:
    """Each conditional write catches `ClientError` to look for one code. Everything else must
    fall through to the shared translation rather than being swallowed."""

    async def test_an_update_refused_for_another_reason_still_translates(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_client_error(
            "update_item",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        with pytest.raises(AccessDeniedError):
            await DynamoDbClient(session).update_item(
                TABLE, ITEM_KEY, set_values={"a": 1}
            )

    async def test_a_delete_refused_for_another_reason_still_translates(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_client_error(
            "delete_item",
            service_error_code="ResourceNotFoundException",
            http_status_code=400,
        )
        with pytest.raises(ResourceNotFoundError):
            await DynamoDbClient(session).delete_item(TABLE, ITEM_KEY)

    async def test_a_transaction_refused_without_reasons_still_translates(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        """A cancellation with no reasons array is left to the general translation rather than
        guessed at."""
        dynamodb_stub.add_client_error(
            "transact_write_items",
            service_error_code="ValidationException",
            http_status_code=400,
        )
        with pytest.raises(AwsRequestError):
            await DynamoDbClient(session).transact_write_items(
                [TransactPut(TABLE, ITEM)]
            )

    async def test_a_transactional_read_refusal_still_translates(
        self, session: AwsSession, dynamodb_stub: Stubber
    ) -> None:
        dynamodb_stub.add_client_error(
            "transact_get_items",
            service_error_code="ThrottlingException",
            http_status_code=400,
        )
        with pytest.raises(ThrottledError):
            await DynamoDbClient(session).transact_get_items(
                [TransactGet(TABLE, ITEM_KEY)]
            )

    async def test_an_oversized_transactional_read_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        with pytest.raises(ValueError, match="at most 100"):
            await DynamoDbClient(session).transact_get_items(
                [TransactGet(TABLE, ITEM_KEY)] * 101
            )
