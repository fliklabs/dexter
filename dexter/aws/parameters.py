"""Reading configuration out of the parameter store.

The sibling of `secrets.py`, and the split between them is a deployment convention worth stating:
a **secret** is one JSON document holding many values under one name, and a **parameter** is one
value under one name in a hierarchy. Table names, queue URLs, endpoints and feature flags are
parameters; passwords and API keys are secret keys. Both arrive at a component as a `ValueSource`
and neither is visible from there, which is the point.

The class is `ParameterStoreClient` rather than `SsmClient` for two reasons. Systems Manager is a
large service and this speaks to one part of it, so the narrower name is the true one; and
`SsmClient` would sit in `session.py` beside `mypy_boto3_ssm`'s own `SSMClient`, where
`SecretsManagerClient` already collides with its stub counterpart once.

**Decryption is on by default**, and that is the one behaviour here worth arguing for. Without
`WithDecryption`, reading a `SecureString` succeeds and returns the *ciphertext* — a plausible
looking string that becomes a wrong configuration value somewhere far away. The cost of leaving
it on is a `kms:Decrypt` the caller almost always wants; the failure when the role lacks it is a
loud `AccessDeniedError` naming the call, which is the better of the two ways to be wrong.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from botocore.exceptions import ClientError

from ._caching import TtlCache
from ._calling import call, error_code
from .errors import ParameterNotFoundError
from .session import AwsSession

MISSING_PARAMETER_CODE = "ParameterNotFound"
"""What `GetParameter` says when the name is not in the store.

`GetParameters` says nothing at all — it answers 200 and lists the names it did not recognise in
an `InvalidParameters` array, which is why the plural form has to check that array by hand.
"""

BATCH_SIZE = 10
"""How many names one `GetParameters` may carry. A hard service limit, not a choice."""


class ParameterStoreClient:
    """Reads parameters and remembers them for a while."""

    __slots__ = ("_cache", "_session")

    def __init__(self, session: AwsSession) -> None:
        """Take the shared boto3 clients; the cache starts empty."""
        self._session = session
        self._cache: TtlCache[str] = TtlCache(session.config.parameter_cache_seconds)

    async def get_parameter(self, name: str, *, decrypt: bool = True) -> str:
        """The value of the parameter called `name`, possibly from cache.

        Raises:
            ParameterNotFoundError: If no such parameter exists.
            AccessDeniedError: If the role may not read it, or may not use its key.
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return await self._cache.get(
            _cache_key(name, decrypt), lambda: self._fetch(name, decrypt)
        )

    async def get_parameters(
        self, names: Sequence[str], *, decrypt: bool = True
    ) -> dict[str, str]:
        """Every named parameter, in one request per ten names.

        Cached values are answered without a request, and only the names actually missing from
        the cache are asked for — so calling this for twenty names of which eighteen are known
        costs one round trip, not two.

        Raises:
            ParameterNotFoundError: If any of `names` does not exist. Named individually, since
                the service reports them as a list and a caller cannot act on "one of these".
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        found: dict[str, str] = {}
        wanted: list[str] = []
        for name in names:
            cached = self._cache.peek(_cache_key(name, decrypt))
            if cached is None:
                wanted.append(name)
            else:
                found[name] = cached[0]

        for start in range(0, len(wanted), BATCH_SIZE):
            chunk = wanted[start : start + BATCH_SIZE]
            found.update(await self._fetch_many(chunk, decrypt))
        return found

    async def get_parameters_by_path(
        self,
        path: str,
        *,
        recursive: bool = True,
        decrypt: bool = True,
    ) -> dict[str, str]:
        """Every parameter under `path`, following every page.

        The whole hierarchy under `/app/production/` is how a deployment usually keeps its
        configuration, and reading it in one call is what makes that arrangement usable. It
        paginates internally — the service returns ten at a time, so a flat read would silently
        see only the first ten.

        **Results are not cached.** The cache is keyed on a parameter's name, and this answers a
        set whose membership can change; storing the individual values would leave a caller who
        later adds a parameter unable to see it until the entries expire, which is the confusing
        half of a cache without the useful half.

        Raises:
            AwsRequestError: If the read was refused or could not be made.
            CredentialsUnavailableError: If this process has no usable identity.
        """
        return {
            name: value async for name, value in self._walk(path, recursive, decrypt)
        }

    def invalidate(self, name: str | None = None, /) -> None:
        """Forget one parameter, or all of them, so the next read fetches again.

        For a change that has to take effect before `AwsConfig.parameter_cache_seconds` is up.
        Performs no I/O and so is not `async`.

        A name given here forgets both its decrypted and its undecrypted entry, because a caller
        holding a name does not think of those as two things.
        """
        if name is None:
            self._cache.invalidate()
            return
        self._cache.invalidate(_cache_key(name, True))
        self._cache.invalidate(_cache_key(name, False))

    async def _fetch(self, name: str, decrypt: bool, /) -> str:
        """Fetch one parameter, with no cache involved."""

        def read() -> str:
            try:
                response = self._session.ssm.get_parameter(
                    Name=name, WithDecryption=decrypt
                )
            except ClientError as error:
                if error_code(error) == MISSING_PARAMETER_CODE:
                    raise ParameterNotFoundError(
                        f"Parameter {name!r} does not exist."
                    ) from error
                raise
            return response["Parameter"].get("Value", "")

        return await call(f"GetParameter {name}", read)

    async def _fetch_many(
        self, names: Sequence[str], decrypt: bool, /
    ) -> dict[str, str]:
        """Fetch up to ten parameters, storing each in the cache as it arrives."""
        response = await call(
            f"GetParameters {', '.join(names)}",
            lambda: self._session.ssm.get_parameters(
                Names=list(names), WithDecryption=decrypt
            ),
        )

        missing = response.get("InvalidParameters", [])
        if missing:
            # Named individually. The service reports them as a list, and a message saying
            # "one of these five is missing" is one the reader has to go and check by hand.
            raise ParameterNotFoundError(
                f"These parameters do not exist: {', '.join(sorted(missing))}."
            )

        values: dict[str, str] = {}
        for parameter in response.get("Parameters", []):
            name = parameter.get("Name", "")
            value = parameter.get("Value", "")
            values[name] = value
            self._cache.put(_cache_key(name, decrypt), value)
        return values

    async def _walk(
        self, path: str, recursive: bool, decrypt: bool, /
    ) -> AsyncIterator[tuple[str, str]]:
        """Every parameter under `path`, one page at a time.

        Stops when the service stops giving a `NextToken`, never when a page looks small: the
        page size is the service's to choose, and a short page is not the last one.
        """
        token: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "Path": path,
                "Recursive": recursive,
                "WithDecryption": decrypt,
            }
            if token is not None:
                arguments["NextToken"] = token

            response = await self._page(path, arguments)
            for parameter in response.get("Parameters", []):
                yield parameter.get("Name", ""), parameter.get("Value", "")

            token = response.get("NextToken")
            if not token:
                return

    async def _page(self, path: str, arguments: dict[str, Any], /) -> Any:
        """One page of a path walk.

        Its own method rather than a lambda built inside the loop, because a closure over a
        loop variable is what ruff's B023 exists to catch — safe here only because it is awaited
        in the same iteration, which is a property one edit away from being false.
        """
        return await call(
            f"GetParametersByPath {path}",
            lambda: self._session.ssm.get_parameters_by_path(**arguments),
        )


def _cache_key(name: str, decrypt: bool, /) -> str:
    """The cache key for one parameter read.

    **The decryption flag is part of the key, and leaving it out would be a real bug.** The same
    name answers different strings under the two flags — the plaintext and the ciphertext — so a
    single key would let an undecrypted read poison what every decrypted one sees.
    """
    return f"{name}\x00{decrypt:d}"


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
