"""The walkthrough. Run with `uv run python -m examples.taskflow`.

Each section below shows one thing the container does that cannot be seen by reading a
signature. Nothing here asserts anything — read the output and judge it.
"""

import asyncio

from dexter.dependency_injection import Container, ResolutionError

from .display import heading, line, note, reset_tags, tag
from .domain import Clock, Job, Notifier, Repository, Settings
from .services import (
    ArchiveJobHandler,
    ConnectionPool,
    JobDispatcher,
    JobHandler,
    RequestContext,
)
from .wiring import build_container

JOBS = (
    Job(id="job-a", payload="resize"),
    Job(id="job-b", payload="transcode"),
    Job(id="job-c", payload="thumbnail"),
)


async def show_startup(container: Container) -> None:
    """Resolve the singleton pool, which an async factory opens exactly once."""
    heading("startup")
    settings = await container.resolve(Settings)
    pool = await container.resolve(ConnectionPool)
    line(f"{tag(pool)} opened   dsn={settings.dsn}  is_open={pool.is_open}")

    again = await container.resolve(ConnectionPool)
    line(f"resolved again -> {tag(again)}")
    note("Singleton: the async factory ran once; both resolves are the same object.")


async def handle_in_own_scope(container: Container, job: Job) -> str:
    """Handle one job inside its own scope and describe what was injected."""
    async with container.scope() as scope:
        dispatcher = await scope.resolve(JobDispatcher)
        result = await dispatcher.dispatch(job)

        pool = await scope.resolve(ConnectionPool)
        repository = await scope.resolve(Repository)
        context = await scope.resolve(RequestContext)
        return (
            f"{job.id}  pool={tag(pool)}  repo={tag(repository)}  "
            f"ctx={tag(context)}  notified={result.notified}"
        )


async def show_concurrent_scopes(container: Container) -> None:
    """Handle every job at once, each in its own scope."""
    heading(f"{len(JOBS)} jobs, handled concurrently, one scope each")
    for described in await asyncio.gather(
        *(handle_in_own_scope(container, job) for job in JOBS)
    ):
        line(described)
    note("pool is identical everywhere — Singleton.")
    note("repo differs per job — Scoped, one per scope.")
    note("ctx differs everywhere — Transient, one per resolution.")


async def show_lifetimes_within_one_scope(container: Container) -> None:
    """Resolve the same keys twice inside a single scope."""
    heading("within a single scope")
    async with container.scope() as scope:
        first_repository = await scope.resolve(Repository)
        second_repository = await scope.resolve(Repository)
        line(
            f"Repository     twice -> {tag(first_repository)}, "
            f"{tag(second_repository)}  same={first_repository is second_repository}"
        )

        first_context = await scope.resolve(RequestContext)
        second_context = await scope.resolve(RequestContext)
        line(
            f"RequestContext twice -> {tag(first_context)}, "
            f"{tag(second_context)}  same={first_context is second_context}"
        )
        note("Scoped is cached within the scope; Transient never is.")


async def show_self_injection(container: Container) -> None:
    """Show that a `Container` parameter yields the resolving scope, not the root."""
    heading("self-injection")
    line(f"the root is        -> {tag(container)}")

    async with container.scope() as scope:
        dispatcher = await scope.resolve(JobDispatcher)
        repository = await scope.resolve(Repository)
        line(f"a scope is         -> {tag(scope)}")
        line(
            f"dispatcher holds   -> {tag(dispatcher.container)}  (the scope, not the root)"
        )

        # The dispatcher resolves its handler from whatever container it was given, so the
        # handler shares this scope's repository rather than some other scope's.
        handler = await dispatcher.container.resolve(JobHandler)
        line(f"its handler's repo -> {tag(handler.repository)}")
        line(f"the scope's repo   -> {tag(repository)}")
        line(f"same repository?   -> {handler.repository is repository}")
    note("A `Container` parameter receives whichever container is resolving.")
    note("This is why the dispatcher is Scoped: a Singleton would capture the root.")


async def show_optional_dependency() -> None:
    """Wire the same application with and without a notifier."""
    heading("optional dependency: Notifier | None")
    for with_notifier in (False, True):
        container = build_container(with_notifier=with_notifier)
        async with container.scope() as scope:
            handler = await scope.resolve(JobHandler)
            injected = "None" if handler.notifier is None else tag(handler.notifier)
            line(f"with_notifier={with_notifier!s:5} -> handler.notifier = {injected}")
            await handler.handle(JOBS[0])
        await container.aclose()
    note("The handler's code never changes; only the wiring does.")


async def show_try_resolve(container: Container) -> None:
    """Probe for a subsystem that may not be wired, without raising."""
    heading("probing for something that may not be wired")
    clock = await container.try_resolve(Clock)
    line(f"try_resolve(Clock)    -> {tag(clock) if clock is not None else 'None'}")
    notifier = await container.try_resolve(Notifier)
    line(
        f"try_resolve(Notifier) -> {tag(notifier) if notifier is not None else 'None'}"
    )
    note(
        "An unbound key yields None instead of raising — this container has no Notifier."
    )


async def show_resolution_failure(container: Container) -> None:
    """Resolve something whose own dependency was never bound."""
    heading("what a resolution failure reports")
    try:
        await container.resolve(ArchiveJobHandler)
    except ResolutionError as error:
        for output_line in str(error).splitlines():
            line(output_line)
        note("The chain is the point: it names the path, not just the missing key.")


async def main() -> None:
    """Run the whole walkthrough."""
    print("dexter · taskflow reference app")
    reset_tags()

    container = build_container()
    try:
        await show_startup(container)
        await show_concurrent_scopes(container)
        await show_lifetimes_within_one_scope(container)
        await show_self_injection(container)
        await show_try_resolve(container)
        await show_resolution_failure(container)
    finally:
        await container.aclose()

    await show_optional_dependency()

    heading("shutdown")
    line("container closed")
    print()


if __name__ == "__main__":
    asyncio.run(main())
