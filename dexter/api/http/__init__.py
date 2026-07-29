"""Serving registered handlers over HTTP.

The only part of `dexter.api` that knows a web framework exists. Importing this package is
what pulls one in, which is why it is a package of its own rather than a few files beside the
rest: the boundary is a directory a test can walk, and a second protocol would sit next to it
with the same guarantee.
"""

from .app import create_app as create_app
