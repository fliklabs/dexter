"""Fetching JSON secrets, and the cache that makes doing so per-call affordable.

**The cache is most of why this class exists.** A value resolved from a secret store is read on
every call that needs it, so an uncached implementation turns each request into two network
round-trips and each secret into a per-request charge. `TtlCache` holds the policy, including the
part that matters under load — one fetch at a time per secret, however many callers arrive
together.
"""

import json
from typing import Any

from .._caching import TtlCache
from .._calling import call
from ..errors import SecretNotFoundError
from ..session import AwsSession


class SecretsManagerClient:
    """Fetches JSON secrets and remembers them for a while."""

    __slots__ = ("_cache", "_session")

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients; the cache starts empty."""
        self._session = session
        self._cache: TtlCache[dict[str, Any]] = TtlCache(
            session.config.secret_cache_seconds
        )

    async def get_secret(self, secret_id: str) -> dict[str, Any]:
        """The whole secret named `secret_id`, parsed, possibly from cache.

        Returns:
            The decoded document. **The returned dictionary is the cached one**, not a copy:
            mutating it corrupts what every later caller sees. Read from it.

        Raises:
            SecretNotFoundError: If no such secret exists, or it holds no JSON object.
            AwsRequestError: If the fetch was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await self._cache.get(secret_id, lambda: self._fetch(secret_id))

    async def get_secret_key(self, secret_id: str, key: str) -> str:
        """One string value out of the secret named `secret_id`.

        Raises:
            SecretNotFoundError: If the secret does not exist, does not hold `key`, or holds
                something there that is not a string.
            AwsRequestError: If the fetch was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        values = await self.get_secret(secret_id)
        if key not in values:
            # The available names are deliberately *not* listed: this message reaches a log,
            # and the set of keys in a secret store is worth keeping to itself.
            raise SecretNotFoundError(f"Secret {secret_id!r} has no key {key!r}.")

        value = values[key]
        if not isinstance(value, str):
            raise SecretNotFoundError(
                f"Secret {secret_id!r} holds {key!r}, but it is a "
                f"{type(value).__name__} rather than a string."
            )
        return value

    def invalidate(self, secret_id: str | None = None, /) -> None:
        """Forget one secret, or all of them, so the next read fetches again.

        For a rotation that has to take effect before `AwsConfig.secret_cache_seconds` is up.
        Performs no I/O and so is not `async`.
        """
        self._cache.invalidate(secret_id)

    async def _fetch(self, secret_id: str, /) -> dict[str, Any]:
        """Fetch and decode one secret, with no cache involved."""
        response = await call(
            f"GetSecretValue {secret_id}",
            lambda: self._session.secrets.get_secret_value(SecretId=secret_id),
        )

        raw = response.get("SecretString")
        if raw is None:
            # A binary secret is a legitimate thing to store and not a thing this reads: there
            # is no general way to turn arbitrary bytes into named string values.
            raise SecretNotFoundError(
                f"Secret {secret_id!r} holds binary data rather than a JSON string."
            )

        try:
            document = json.loads(raw)
        except ValueError as error:
            raise SecretNotFoundError(
                f"Secret {secret_id!r} is not valid JSON."
            ) from error

        if not isinstance(document, dict):
            raise SecretNotFoundError(
                f"Secret {secret_id!r} is a {type(document).__name__} rather than a JSON "
                f"object of named values."
            )
        return document
