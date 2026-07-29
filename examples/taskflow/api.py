"""An HTTP edge over the same container, added without touching anything else.

Worth reading for what is *not* here. taskflow has no CQRS — no bus, no command, no event —
and `dexter.api` neither knows nor cares. A handler asks for the same `JobDispatcher` and the
same `ConnectionPool` the walkthrough resolves by hand, and the container does the rest.

The two endpoints are chosen to make scope visible over HTTP, which is the thing this example
exists to teach. `ConnectionPool` is a singleton, so every request reports the same one.
`Repository` is scoped, so each request gets its own and the count never climbs past what that
one request did. Both facts are invisible in a printed transcript and obvious from a browser.
"""

from http import HTTPMethod, HTTPStatus

from pydantic import BaseModel, ConfigDict, Field

from dexter.api import HttpExposure, register_handler, use_api
from dexter.dependency_injection import ContainerBuilder, Scope

from .domain import Job, JobResult, Repository
from .services import ConnectionPool, JobDispatcher


class DispatchJobRequest(BaseModel):
    """The body of a dispatch request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Whatever you want to call this job.")
    payload: str = Field(default="", description="Anything the handler should see.")


class ScopeReport(BaseModel):
    """What one request could see of the container it was served from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pool_dsn: str
    pool_is_open: bool
    results_in_this_scope: int


class DispatchJobApi:
    """Hand a job to the dispatcher and report what came back."""

    def __init__(self, dispatcher: JobDispatcher) -> None:
        """Take the dispatcher, which is scoped and resolves handlers from this request."""
        self.dispatcher = dispatcher

    async def handle(self, request: DispatchJobRequest) -> JobResult:
        """Dispatch the job.

        `notified` in the response is `True` only when a `Notifier` was bound — the optional
        dependency, visible from outside.
        """
        return await self.dispatcher.dispatch(
            Job(id=request.id, payload=request.payload)
        )


class ScopeReportRequest(BaseModel):
    """Takes nothing: everything reported comes from what was injected."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScopeReportApi:
    """Report what this request's scope holds."""

    def __init__(self, pool: ConnectionPool, repository: Repository) -> None:
        """Take one singleton and one scoped dependency, to contrast them."""
        self.pool = pool
        self.repository = repository

    async def handle(self, request: ScopeReportRequest) -> ScopeReport:  # noqa: ARG002
        """Describe the pool every request shares, and the repository each request gets."""
        return ScopeReport(
            pool_dsn=self.pool.dsn,
            pool_is_open=self.pool.is_open,
            results_in_this_scope=await self.repository.count(),
        )


def register_api(builder: ContainerBuilder) -> None:
    """Add the HTTP edge to a builder the rest of taskflow has already been wired into."""
    use_api(builder)

    register_handler(
        builder,
        DispatchJobApi,
        HttpExposure(
            method=HTTPMethod.POST,
            path="/jobs",
            status=HTTPStatus.CREATED,
            tags=("taskflow",),
        ),
        scope=Scope.TRANSIENT,
    )
    register_handler(
        builder,
        ScopeReportApi,
        HttpExposure(method=HTTPMethod.GET, path="/scope", tags=("taskflow",)),
        scope=Scope.TRANSIENT,
    )
