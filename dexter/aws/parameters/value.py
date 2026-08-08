"""One parameter, as a `ValueSource`.

Its own file for the same reason as `secrets/value.py`: the client is *how a parameter is
fetched*, and this is *where one value lives*. Only the second half reaches a consumer's
constructor.
"""

from .client import ParameterStoreClient


class ParameterValue:
    """A `ValueSource` over one parameter.

    What a deployment binds in place of a `StaticValue` for a table name, a queue URL or an
    endpoint. It holds a location and no value; `ParameterStoreClient` holds the cache, once, for
    every value naming the same parameter.
    """

    __slots__ = ("_client", "_decrypt", "_name")

    def __init__(
        self, client: ParameterStoreClient, name: str, *, decrypt: bool = True
    ) -> None:
        """Name where the value lives.

        Args:
            client: The fetcher, and so the cache.
            name: The parameter's full name, such as `/app/production/orders-table`.
            decrypt: Whether to ask the service to decrypt a `SecureString`.
        """
        self._client = client
        self._name = name
        self._decrypt = decrypt

    async def value(self) -> str:
        """Fetch the value, usually from cache.

        Raises:
            ParameterNotFoundError: If the parameter does not exist.
            AwsRequestError: If the fetch was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await self._client.get_parameter(self._name, decrypt=self._decrypt)

    def __repr__(self) -> str:
        """Name where the value comes from, which is a location rather than a secret."""
        return f"{type(self).__name__}({self._name!r})"
