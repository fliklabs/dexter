"""Building the botocore exceptions these tests need, correctly.

`ClientError` takes a response dictionary rather than a message, and getting its shape wrong
produces an exception that tests something other than what the service actually raises. One
factory, so every test agrees on the shape.
"""

from typing import Any

from botocore.exceptions import ClientError


def client_error(code: str | None, operation: str) -> ClientError:
    """A `ClientError` shaped the way botocore builds one.

    Args:
        code: The service's short code, such as `NoSuchKey`. `None` builds a response with no
            `Error` block at all — malformed, and worth covering because a caller reading the
            code has to survive it.
        operation: The API operation name, which botocore puts in the string form.
    """
    response: dict[str, Any] = {} if code is None else {"Error": {"Code": code}}
    return ClientError(response, operation)  # type: ignore[arg-type]
