"""A document store.

The largest group in this module, and a package because four sub-concepts each earned a name:
the type policy, the expression compiler, paging, and batch retry.

`Attr` and `Key` are re-exported here so that a consumer writing a query imports the vocabulary
from the place they are using::

    from dexter.aws.dynamodb import Attr, DynamoDbClient, Key
"""

from ..models import Attr as Attr
from ..models import Condition as Condition
from ..models import Key as Key
from .client import DynamoDbClient as DynamoDbClient
from .paging import ItemStream as ItemStream
