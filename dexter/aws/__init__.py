"""Talking to AWS: storage, documents, secrets, parameters, mail, notifications and queues.

Seven clients over one shared boto3 session, every method asynchronous, and no boto3 type on any
signature::

    class StorePhoto:
        def __init__(self, storage: S3Client) -> None:
            self.storage = storage

        async def handle(self, request: Upload) -> Stored:
            url = await self.storage.presigned_put_url(
                BUCKET, f"items/{request.item_id}/photo.jpg", content_type="image/jpeg"
            )
            return Stored(upload_url=url)

Wiring is two calls, and the second is the application's::

    use_aws(builder)
    register_aws_config(builder, AwsConfig(region="ap-southeast-2"))

**Configuration values come through `ValueSource`, which is the module's other half.** A
component that needs a table name or a password depends on the *value*, never on the store it
lives in, so the same code runs against a settings object locally and Secrets Manager in
production. See `dexter/aws/values.py`.

**boto3 rather than a hand-rolled client, deliberately.** `dexter.notification.resend` speaks one
documented POST and skips the vendor SDK, and the reasoning does not carry here: signing an AWS
request is signature version 4 over a canonicalised request, and reaching AWS at all means a
credential chain covering environment variables, profiles, `credential_process`, container
endpoints and instance metadata, with refresh. Every one of those is code whose best possible
outcome is behaving exactly like boto3 does. See `dexter/aws/session.py`.

**Credentials are not configured here, and cannot be.** There is no key on `AwsConfig` and no
argument for one. A process outside AWS gets short-lived credentials by pointing
`AWS_EC2_METADATA_SERVICE_ENDPOINT` at something speaking IMDSv2 — IAM Roles Anywhere's
`aws_signing_helper serve` does — and boto3 finds it with nothing asked of this module.

**The cost of the SDK being synchronous is paid in one file.** `dexter/aws/_calling.py` runs each
call with `asyncio.to_thread` and translates what botocore raises into this module's error tree,
so the public surface is `async def` throughout and no `ClientError` escapes. That file also
documents the limit the arrangement imposes: the default executor caps in-flight calls at about
thirty-two, whatever the connection pool says.
"""

from ._caching import TtlCache as TtlCache
from .dynamodb import DynamoDbClient as DynamoDbClient
from .dynamodb import ItemStream as ItemStream
from .errors import AccessDeniedError as AccessDeniedError
from .errors import AwsError as AwsError
from .errors import AwsRequestError as AwsRequestError
from .errors import AwsWiringError as AwsWiringError
from .errors import BatchIncompleteError as BatchIncompleteError
from .errors import ConditionFailedError as ConditionFailedError
from .errors import CredentialsUnavailableError as CredentialsUnavailableError
from .errors import EmailRejectedError as EmailRejectedError
from .errors import ItemEncodingError as ItemEncodingError
from .errors import MessageTooLargeError as MessageTooLargeError
from .errors import ObjectNotFoundError as ObjectNotFoundError
from .errors import ParameterNotFoundError as ParameterNotFoundError
from .errors import ResourceNotFoundError as ResourceNotFoundError
from .errors import SecretNotFoundError as SecretNotFoundError
from .errors import ThrottledError as ThrottledError
from .errors import TransactionConflictError as TransactionConflictError
from .models import And as And
from .models import Attr as Attr
from .models import AttributeType as AttributeType
from .models import AwsConfig as AwsConfig
from .models import AwsEndpoints as AwsEndpoints
from .models import BatchFailure as BatchFailure
from .models import BatchResult as BatchResult
from .models import BatchSuccess as BatchSuccess
from .models import BeginsWith as BeginsWith
from .models import Between as Between
from .models import Comparison as Comparison
from .models import ComparisonOperator as ComparisonOperator
from .models import Condition as Condition
from .models import Contains as Contains
from .models import DeleteFailure as DeleteFailure
from .models import DeleteReport as DeleteReport
from .models import DeleteRequest as DeleteRequest
from .models import Exists as Exists
from .models import In as In
from .models import Item as Item
from .models import ItemKey as ItemKey
from .models import ItemPage as ItemPage
from .models import Key as Key
from .models import MessageAttribute as MessageAttribute
from .models import Not as Not
from .models import NotExists as NotExists
from .models import ObjectPage as ObjectPage
from .models import ObjectSummary as ObjectSummary
from .models import Or as Or
from .models import OutboundMessage as OutboundMessage
from .models import PutRequest as PutRequest
from .models import ReceivedMessage as ReceivedMessage
from .models import RetryMode as RetryMode
from .models import SmsType as SmsType
from .models import TransactConditionCheck as TransactConditionCheck
from .models import TransactDelete as TransactDelete
from .models import TransactGet as TransactGet
from .models import TransactPut as TransactPut
from .models import TransactUpdate as TransactUpdate
from .models import TransactWrite as TransactWrite
from .models import WriteRequest as WriteRequest
from .models import describe_comparison_operator as describe_comparison_operator
from .models import describe_retry_mode as describe_retry_mode
from .models import describe_sms_type as describe_sms_type
from .parameters import ParameterStoreClient as ParameterStoreClient
from .parameters import ParameterValue as ParameterValue
from .s3 import S3Client as S3Client
from .secrets import SecretsManagerClient as SecretsManagerClient
from .secrets import SecretValue as SecretValue
from .ses import SesClient as SesClient
from .session import AwsSession as AwsSession
from .sns import SnsClient as SnsClient
from .sqs import SqsClient as SqsClient
from .use import register_aws_config as register_aws_config
from .use import register_parameter_value as register_parameter_value
from .use import register_secret_value as register_secret_value
from .use import use_aws as use_aws
from .values import StaticValue as StaticValue
from .values import ValueSource as ValueSource
