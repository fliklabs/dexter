"""SSM Parameter Store.

The sibling of `dexter.aws.secrets`, and the split between them is a deployment convention worth
stating: a **secret** is one JSON document holding many values under one name, and a **parameter**
is one value under one name in a hierarchy. Table names, queue URLs, endpoints and feature flags
are parameters; passwords and API keys are secret keys.

Both arrive at a component as a `ValueSource` and neither is visible from there, which is the
point — a repository asking for its table name cannot tell which store answered, or whether one
did at all.
"""

from .client import ParameterStoreClient as ParameterStoreClient
from .value import ParameterValue as ParameterValue
