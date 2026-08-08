"""Covers `dexter.aws.sqs`: sending, receiving, finishing, and what a partial batch reports.

The batch tests carry the weight. SQS answers a batch with HTTP 200 and lists the refused entries
inside the body, so the interesting assertions are about what comes back from a *successful*
request — and about the entry ids, which are generated here and must map back to the caller's own
indexes or the report names the wrong message.
"""

from datetime import UTC, datetime

import pytest
from botocore.stub import Stubber

from dexter.aws import (
    AwsSession,
    MessageTooLargeError,
    OutboundMessage,
    SqsClient,
)

QUEUE = "https://sqs.ap-southeast-2.amazonaws.com/123456789012/orders"
FIFO_QUEUE = "https://sqs.ap-southeast-2.amazonaws.com/123456789012/orders.fifo"
HANDLE = "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a=="
MESSAGE_ID = "11111111-2222-3333-4444-555555555555"


class TestQueueUrl:
    async def test_resolves_a_name(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "get_queue_url", {"QueueUrl": QUEUE}, {"QueueName": "orders"}
        )
        assert await SqsClient(session).queue_url("orders") == QUEUE

    async def test_looks_a_name_up_only_once(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """A queue's URL is derived from its name and account and does not change."""
        sqs_stub.add_response(
            "get_queue_url", {"QueueUrl": QUEUE}, {"QueueName": "orders"}
        )
        client = SqsClient(session)

        assert await client.queue_url("orders") == QUEUE
        assert await client.queue_url("orders") == QUEUE


class TestSending:
    async def test_sends_one_message(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message",
            {"MessageId": MESSAGE_ID},
            {"QueueUrl": QUEUE, "MessageBody": "hello", "DelaySeconds": 0},
        )
        assert await SqsClient(session).send_message(QUEUE, "hello") == MESSAGE_ID

    async def test_carries_a_delay_and_attributes(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message",
            {"MessageId": MESSAGE_ID},
            {
                "QueueUrl": QUEUE,
                "MessageBody": "hello",
                "DelaySeconds": 30,
                "MessageAttributes": {
                    "kind": {"DataType": "String", "StringValue": "order"}
                },
            },
        )
        await SqsClient(session).send_message(
            QUEUE, "hello", delay_seconds=30, attributes={"kind": "order"}
        )

    async def test_carries_fifo_ordering_and_deduplication(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message",
            {"MessageId": MESSAGE_ID},
            {
                "QueueUrl": FIFO_QUEUE,
                "MessageBody": "hello",
                "DelaySeconds": 0,
                "MessageGroupId": "customer-1",
                "MessageDeduplicationId": "order-1",
            },
        )
        await SqsClient(session).send_message(
            FIFO_QUEUE, "hello", group_id="customer-1", deduplication_id="order-1"
        )


class TestSendingBatches:
    async def test_generates_entry_ids_and_reports_the_caller_s_indexes(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """Entry ids mean nothing outside the request, so what comes back is a position in
        what the caller passed."""
        sqs_stub.add_response(
            "send_message_batch",
            {
                "Successful": [
                    {
                        "Id": "0",
                        "MessageId": "m-0",
                        "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                    },
                    {
                        "Id": "1",
                        "MessageId": "m-1",
                        "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                    },
                ],
                "Failed": [],
            },
            {
                "QueueUrl": QUEUE,
                "Entries": [
                    {"Id": "0", "MessageBody": "a", "DelaySeconds": 0},
                    {"Id": "1", "MessageBody": "b", "DelaySeconds": 0},
                ],
            },
        )

        result = await SqsClient(session).send_messages(
            QUEUE, [OutboundMessage(body="a"), OutboundMessage(body="b")]
        )

        assert [entry.index for entry in result.succeeded] == [0, 1]
        assert [entry.message_id for entry in result.succeeded] == ["m-0", "m-1"]
        assert result.failed == ()

    async def test_reports_a_partial_failure_rather_than_raising(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """**The whole reason this returns a report.**

        The request succeeded; one entry did not. A method returning `None` would discard the
        half the caller has to act on.
        """
        sqs_stub.add_response(
            "send_message_batch",
            {
                "Successful": [
                    {
                        "Id": "0",
                        "MessageId": "m-0",
                        "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                    }
                ],
                "Failed": [
                    {
                        "Id": "1",
                        "Code": "InvalidParameterValue",
                        "Message": "too big",
                        "SenderFault": True,
                    }
                ],
            },
            {
                "QueueUrl": QUEUE,
                "Entries": [
                    {"Id": "0", "MessageBody": "a", "DelaySeconds": 0},
                    {"Id": "1", "MessageBody": "b", "DelaySeconds": 0},
                ],
            },
        )

        result = await SqsClient(session).send_messages(
            QUEUE, [OutboundMessage(body="a"), OutboundMessage(body="b")]
        )

        assert [entry.index for entry in result.failed] == [1]
        assert result.failed[0].sender_fault is True
        assert result.failed[0].code == "InvalidParameterValue"

    async def test_chunks_at_ten_and_keeps_indexes_across_chunks(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """Eleven messages is two requests, and the eleventh must come back as index 10 rather
        than as index 0 of the second chunk."""
        messages = [OutboundMessage(body=str(index)) for index in range(11)]
        for chunk_start, size in ((0, 10), (10, 1)):
            sqs_stub.add_response(
                "send_message_batch",
                {
                    "Successful": [
                        {
                            "Id": str(offset),
                            "MessageId": f"m-{chunk_start + offset}",
                            "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                        }
                        for offset in range(size)
                    ],
                    "Failed": [],
                },
                {
                    "QueueUrl": QUEUE,
                    "Entries": [
                        {
                            "Id": str(offset),
                            "MessageBody": str(chunk_start + offset),
                            "DelaySeconds": 0,
                        }
                        for offset in range(size)
                    ],
                },
            )

        result = await SqsClient(session).send_messages(QUEUE, messages)
        assert [entry.index for entry in result.succeeded] == list(range(11))

    async def test_carries_per_message_attributes(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message_batch",
            {
                "Successful": [
                    {
                        "Id": "0",
                        "MessageId": "m-0",
                        "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                    }
                ],
                "Failed": [],
            },
            {
                "QueueUrl": QUEUE,
                "Entries": [
                    {
                        "Id": "0",
                        "MessageBody": "a",
                        "DelaySeconds": 0,
                        "MessageAttributes": {
                            "kind": {"DataType": "String", "StringValue": "order"}
                        },
                    }
                ],
            },
        )
        await SqsClient(session).send_messages(
            QUEUE, [OutboundMessage.of("a", attributes={"kind": "order"})]
        )


class TestReceiving:
    async def test_long_polls_and_asks_for_every_attribute(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """Both are corrections. Short polling returns empty while messages wait, and SQS sends
        neither attribute set unless asked — including the receive count a consumer needs to
        recognise a poison message."""
        sqs_stub.add_response(
            "receive_message",
            {"Messages": []},
            {
                "QueueUrl": QUEUE,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "MessageSystemAttributeNames": ["All"],
                "MessageAttributeNames": ["All"],
            },
        )
        assert await SqsClient(session).receive_messages(QUEUE) == ()

    async def test_reads_a_message_into_its_own_shape(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": MESSAGE_ID,
                        "ReceiptHandle": HANDLE,
                        "Body": "hello",
                        "Attributes": {
                            "ApproximateReceiveCount": "3",
                            "SentTimestamp": "1754640000000",
                        },
                        "MessageAttributes": {
                            "kind": {"DataType": "String", "StringValue": "order"}
                        },
                    }
                ]
            },
            {
                "QueueUrl": QUEUE,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "MessageSystemAttributeNames": ["All"],
                "MessageAttributeNames": ["All"],
            },
        )

        (message,) = await SqsClient(session).receive_messages(QUEUE)

        assert message.message_id == MESSAGE_ID
        assert message.receipt_handle == HANDLE
        assert message.body == "hello"
        assert message.approximate_receive_count == 3
        assert message.attributes[0].name == "kind"
        assert message.attributes[0].value == "order"
        assert message.sent_at == datetime(2025, 8, 8, 8, 0, tzinfo=UTC)

    async def test_the_sent_timestamp_is_timezone_aware(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """A naive datetime here is one the caller has to guess the zone of."""
        sqs_stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": MESSAGE_ID,
                        "ReceiptHandle": HANDLE,
                        "Body": "hello",
                        "Attributes": {"SentTimestamp": "1754640000000"},
                    }
                ]
            },
            {
                "QueueUrl": QUEUE,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "MessageSystemAttributeNames": ["All"],
                "MessageAttributeNames": ["All"],
            },
        )

        (message,) = await SqsClient(session).receive_messages(QUEUE)
        assert message.sent_at is not None
        assert message.sent_at.tzinfo is not None

    async def test_a_short_poll_and_a_visibility_override_are_honoured(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "receive_message",
            {"Messages": []},
            {
                "QueueUrl": QUEUE,
                "MaxNumberOfMessages": 1,
                "WaitTimeSeconds": 0,
                "MessageSystemAttributeNames": ["All"],
                "MessageAttributeNames": ["All"],
                "VisibilityTimeout": 120,
            },
        )
        await SqsClient(session).receive_messages(
            QUEUE, max_messages=1, wait_seconds=0, visibility_timeout_seconds=120
        )


class TestFinishing:
    async def test_deletes_one_message(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "delete_message", {}, {"QueueUrl": QUEUE, "ReceiptHandle": HANDLE}
        )
        await SqsClient(session).delete_message(QUEUE, HANDLE)

    async def test_deletes_many_and_reports_what_failed(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """A refused delete matters more than it looks: that message comes back."""
        sqs_stub.add_response(
            "delete_message_batch",
            {
                "Successful": [{"Id": "0"}],
                "Failed": [
                    {
                        "Id": "1",
                        "Code": "ReceiptHandleIsInvalid",
                        "Message": "expired",
                        "SenderFault": True,
                    }
                ],
            },
            {
                "QueueUrl": QUEUE,
                "Entries": [
                    {"Id": "0", "ReceiptHandle": "a"},
                    {"Id": "1", "ReceiptHandle": "b"},
                ],
            },
        )

        result = await SqsClient(session).delete_messages(QUEUE, ["a", "b"])

        assert [entry.index for entry in result.succeeded] == [0]
        assert [entry.index for entry in result.failed] == [1]

    async def test_a_delete_reports_no_message_id(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """The operation produces none, which is why `BatchSuccess.message_id` is optional."""
        sqs_stub.add_response(
            "delete_message_batch",
            {"Successful": [{"Id": "0"}], "Failed": []},
            {"QueueUrl": QUEUE, "Entries": [{"Id": "0", "ReceiptHandle": "a"}]},
        )
        result = await SqsClient(session).delete_messages(QUEUE, ["a"])
        assert result.succeeded[0].message_id is None

    async def test_changes_one_message_s_visibility(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": QUEUE, "ReceiptHandle": HANDLE, "VisibilityTimeout": 300},
        )
        await SqsClient(session).change_message_visibility(
            QUEUE, HANDLE, visibility_timeout_seconds=300
        )

    async def test_changes_many_messages_visibility(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "change_message_visibility_batch",
            {"Successful": [{"Id": "0"}], "Failed": []},
            {
                "QueueUrl": QUEUE,
                "Entries": [{"Id": "0", "ReceiptHandle": "a", "VisibilityTimeout": 0}],
            },
        )
        result = await SqsClient(session).change_messages_visibility(
            QUEUE, ["a"], visibility_timeout_seconds=0
        )
        assert [entry.index for entry in result.succeeded] == [0]


class TestGuard:
    async def test_a_fifo_queue_without_a_group_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        """SQS answers `MissingParameter`, which names the parameter but not the reason — and
        the reason is visible right there in the URL."""
        with pytest.raises(ValueError, match="FIFO"):
            await SqsClient(session).send_message(FIFO_QUEUE, "hello")

    async def test_a_fifo_batch_without_a_group_is_refused_locally(
        self, session: AwsSession
    ) -> None:
        with pytest.raises(ValueError, match="FIFO"):
            await SqsClient(session).send_messages(
                FIFO_QUEUE, [OutboundMessage(body="hello")]
            )

    async def test_a_standard_queue_needs_no_group(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message",
            {"MessageId": MESSAGE_ID},
            {"QueueUrl": QUEUE, "MessageBody": "hello", "DelaySeconds": 0},
        )
        await SqsClient(session).send_message(QUEUE, "hello")

    async def test_refuses_an_oversized_message(self, session: AwsSession) -> None:
        with pytest.raises(MessageTooLargeError, match="262144"):
            await SqsClient(session).send_message(QUEUE, "x" * 262145)

    async def test_refuses_a_batch_whose_total_is_too_large(
        self, session: AwsSession
    ) -> None:
        """**The limit is on the request, not on each entry.** Ten individually legal messages
        can be refused together, with an error naming none of them.
        """
        messages = [OutboundMessage(body="x" * 30000) for _ in range(10)]
        with pytest.raises(MessageTooLargeError, match="across one request"):
            await SqsClient(session).send_messages(QUEUE, messages)

    def test_an_empty_body_is_refused_when_the_message_is_built(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            OutboundMessage(body="")


class TestBatchEntryDetails:
    async def test_a_batch_entry_carries_its_fifo_ids_and_delay(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        sqs_stub.add_response(
            "send_message_batch",
            {
                "Successful": [
                    {
                        "Id": "0",
                        "MessageId": "m-0",
                        "MD5OfMessageBody": "d41d8cd98f00b204e9800998ecf8427e",
                    }
                ],
                "Failed": [],
            },
            {
                "QueueUrl": FIFO_QUEUE,
                "Entries": [
                    {
                        "Id": "0",
                        "MessageBody": "a",
                        "DelaySeconds": 5,
                        "MessageGroupId": "customer-1",
                        "MessageDeduplicationId": "order-1",
                    }
                ],
            },
        )
        await SqsClient(session).send_messages(
            FIFO_QUEUE,
            [
                OutboundMessage(
                    body="a",
                    delay_seconds=5,
                    group_id="customer-1",
                    deduplication_id="order-1",
                )
            ],
        )

    async def test_a_message_without_a_sent_timestamp_has_no_sent_at(
        self, session: AwsSession, sqs_stub: Stubber
    ) -> None:
        """SQS omits it when the system attributes were not asked for by a different client."""
        sqs_stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {"MessageId": MESSAGE_ID, "ReceiptHandle": HANDLE, "Body": "hello"}
                ]
            },
            {
                "QueueUrl": QUEUE,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "MessageSystemAttributeNames": ["All"],
                "MessageAttributeNames": ["All"],
            },
        )

        (message,) = await SqsClient(session).receive_messages(QUEUE)
        assert message.sent_at is None
        assert message.approximate_receive_count == 1
