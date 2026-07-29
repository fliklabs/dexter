"""The reference service, and the two ways to run it.

A folder rather than two files beside the other commands, because they are one concept: the
same container built from the same modules, put behind a socket or driven directly. `test` and
`verify` are things this repository does; these are two shapes of one thing it ships.
"""

from .web import web as web
from .worker import worker as worker
