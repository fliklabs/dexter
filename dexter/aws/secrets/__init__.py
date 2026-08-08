"""Secrets Manager.

A secret here is **one JSON document holding many keys** — `{"DATABASE_PASSWORD": ..., ...}`
under a name like `app/production/secrets` — rather than one secret per value. That is a
deployment convention rather than a rule of the service, and it is the one this supports directly
because it is what makes provisioning a new key an edit rather than another infrastructure
resource. It is also why one fetch serves every value a process needs: ten `SecretValue`s over
one secret cost one request between them.
"""

from .client import SecretsManagerClient as SecretsManagerClient
from .value import SecretValue as SecretValue
