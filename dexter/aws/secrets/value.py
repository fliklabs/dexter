"""One key of one secret, as a `ValueSource`.

Its own file rather than a second class beside the client, because the two answer different
questions. `SecretsManagerClient` is *how a secret is fetched*, and a component that wants a
password should never see it; `SecretValue` is *where one value lives*, and is the only half
that crosses into a consumer's constructor.
"""

from .client import SecretsManagerClient


class SecretValue:
    """A `ValueSource` over one key of one JSON secret.

    This is what a deployment binds in place of a `StaticValue`, and the substitution is the
    whole design: a component holds one of these and never learns which of the two it has, so the
    same code runs against a developer's settings file and a production secret store.

    It holds no value of its own and does no caching — `SecretsManagerClient` does both, once,
    for every value pointing at the same secret.
    """

    __slots__ = ("_client", "_key", "_secret_id")

    def __init__(self, client: SecretsManagerClient, secret_id: str, key: str) -> None:
        """Name where the value lives.

        Args:
            client: The fetcher, and so the cache.
            secret_id: The secret's name, such as `app/production/secrets`.
            key: The name inside it, such as `DATABASE_PASSWORD`.
        """
        self._client = client
        self._secret_id = secret_id
        self._key = key

    async def value(self) -> str:
        """Fetch the value, usually from cache.

        Raises:
            SecretNotFoundError: If the secret or the key is missing.
            AwsRequestError: If the fetch was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await self._client.get_secret_key(self._secret_id, self._key)

    def __repr__(self) -> str:
        """Name where the value comes from, which is not itself a secret.

        The rule this and `StaticValue` split between them: an implementation holding the value
        discloses nothing, and one holding only a location names it. A secret's name and a key's
        name are locations, and printing them is what makes a misconfiguration readable in a log.
        """
        return f"{type(self).__name__}({self._secret_id!r}, {self._key!r})"
