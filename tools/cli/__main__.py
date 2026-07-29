"""The entry point, and the only place in this repository that starts an event loop.

That is not an accident. dexter never drives a loop on a caller's behalf, so `dexter.cli.run`
is a coroutine and the bridge from a synchronous process lives here, in the consumer — which
is exactly three lines of it.
"""

import asyncio
import sys

from dexter.cli import run

from .wiring import build_container

PROG_NAME = "./dx"


async def main() -> int:
    """Build the container, run the CLI, and close everything down."""
    container = build_container()
    try:
        return await run(
            container,
            sys.argv[1:],
            prog_name=PROG_NAME,
            title="dexter repo CLI",
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
