"""What S3 says when an object is not there.

One place, because three operations have to recognise it and each answers differently — `GET`
raises, `HEAD` returns `None`, and a copy refuses to delete anything. Getting the set wrong makes
a missing object look like a permission failure, which is the difference between "the upload
never finished" and "the bucket policy is broken".
"""

from botocore.exceptions import ClientError

from .._calling import error_code

MISSING_OBJECT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
"""What S3 says when an object is not there, which is three different things.

`GetObject` answers `NoSuchKey`. `HeadObject` has no body to put a code in, so botocore
synthesises one from the status and it arrives as `404` — or as `NotFound`, depending on the
version. Matching one of the three and letting the others through is how a missing object comes
back as a permission error.
"""


def is_missing(error: ClientError, /) -> bool:
    """Whether this failure means the object is simply not there."""
    return error_code(error) in MISSING_OBJECT_CODES
