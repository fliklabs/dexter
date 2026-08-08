"""Handing out URLs that let somebody else read or write an object.

**A presigned URL is a signature over a request that has not been made yet**, so a browser can
`PUT` a photo straight into a bucket without the credential ever leaving the server, and a third
party can `GET` one without being given an identity at all. Two consequences fall out of that and
both are load-bearing:

- **Nothing large has to pass through the application.** A phone photo is several megabytes; a
  server that receives it, holds it in memory and forwards it is doing work that buys nothing,
  and any reverse proxy in front of it has a body-size limit that must then be raised.
- **A signed `GET` is how something outside the network reads a private object.** A reverse image
  search takes a URL, not bytes.

**Signing performs no I/O.** It is arithmetic over the request and the credentials, and the only
reason these are `async` is that everything else in the module is. Credentials must nonetheless
be resolvable, and a URL signed with a short-lived session is void when that session expires — so
an `expires_in_seconds` longer than the session buys nothing.

Its own file because it shares nothing with reading and writing objects but the client, and
because "this reaches no network" is a property worth being able to state about a whole file.

**Nothing here is cached, deliberately.** Caching a signed URL looks free and is not: the URL
dies with the credentials that signed it, so a cached one expires at a moment unrelated to the
lifetime it was asked for, and the failure is a 403 on a URL whose own query string says it is
still valid.
"""

from .._calling import call
from ..session import AwsSession


async def presigned_get_url(
    session: AwsSession,
    bucket: str,
    key: str,
    *,
    expires_in_seconds: int = 3600,
    filename: str | None = None,
) -> str:
    """A URL that reads `key`, usable by anyone holding it until it expires.

    Args:
        session: The boto3 clients to sign with.
        bucket: The bucket the object is in.
        key: The object to read. **It is not checked for existence** — signing is arithmetic,
            and a URL for a missing object is a perfectly valid signature that answers 404. Call
            `head_object` first where that matters.
        expires_in_seconds: How long the URL stays valid. Longer than the signing session's own
            lifetime buys nothing: the URL dies with the credentials that signed it, which on a
            short-lived identity is usually within the hour.
        filename: When given, the URL asks the browser to download rather than display, under
            this name. **Leave it unset for anything meant to be rendered** — a content
            disposition of `attachment` turns an image into a download.

    Raises:
        AwsRequestError: If a URL could not be signed.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    params: dict[str, str] = {"Bucket": bucket, "Key": key}
    if filename is not None:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return await call(
        f"presign GET s3://{bucket}/{key}",
        lambda: session.s3.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in_seconds
        ),
    )


async def presigned_put_url(
    session: AwsSession,
    bucket: str,
    key: str,
    *,
    content_type: str,
    expires_in_seconds: int = 3600,
) -> str:
    """A URL that writes `key`, usable by a browser with no AWS identity.

    Args:
        session: The boto3 clients to sign with.
        bucket: The bucket to write into.
        key: Where the upload will land. Build it here, from an identifier this application
            minted — never from a filename the client sent.
        content_type: **Part of the signature, not a suggestion.** The upload must send a
            `Content-Type` header matching this exactly or S3 rejects it as a signature
            mismatch, which surfaces in a browser as an opaque 403 with no CORS headers on it.
            Whatever mints this URL and whatever performs the `PUT` have to agree.
        expires_in_seconds: How long the URL stays valid.

    Raises:
        AwsRequestError: If a URL could not be signed.
        CredentialsUnavailableError: If this process has no usable identity.
    """
    return await call(
        f"presign PUT s3://{bucket}/{key}",
        lambda: session.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in_seconds,
        ),
    )
