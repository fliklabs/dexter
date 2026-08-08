"""Object storage.

A package rather than a file because two sub-concepts earned their own names: pagination, which
is what stops a listing from silently truncating, and presigning, which reaches no network at all
and shares nothing with the rest.

`S3Client` is the whole public surface; `ObjectStream` is named here because `list_objects`
returns one and a caller annotating it needs to be able to say so.
"""

from .client import S3Client as S3Client
from .listing import ObjectStream as ObjectStream
