"""taskflow — a simulated async job service wired with dexter's DI container.

Read `wiring.py` first: it is where every dependency is bound, and it is the file worth
copying into a real application. `__main__.py` then walks through what the container does,
printing enough to see the difference between the three scopes.

Run it with::

    uv run python -m examples.taskflow
"""
