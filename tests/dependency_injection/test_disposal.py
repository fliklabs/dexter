"""Disposal: what a container releases when it closes, in what order, and what it refuses."""

from typing import Any

import pytest

from dexter.dependency_injection import (
    ContainerBuilder,
    DisposalError,
    InvalidRegistrationError,
    Scope,
)


class Resource:
    """Something worth releasing, which records that it was."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class Leaf:
    pass


class Middle:
    def __init__(self, leaf: Leaf) -> None:
        self.leaf = leaf


class Top:
    def __init__(self, middle: Middle) -> None:
        self.middle = middle


def make_recorder(log: list[str], name: str) -> Any:
    """Return a sync dispose callback that appends `name` when it runs."""

    def record(_: Any) -> None:
        log.append(name)

    return record


class TestReleasing:
    async def test_disposes_a_singleton_when_the_container_closes(self) -> None:
        builder = ContainerBuilder()
        builder.register(Resource).to(
            Resource, scope=Scope.SINGLETON, dispose=Resource.aclose
        )
        container = builder.build()
        resource = await container.resolve(Resource)

        await container.aclose()

        assert resource.closed is True

    async def test_disposes_a_scoped_instance_when_its_scope_closes(self) -> None:
        builder = ContainerBuilder()
        builder.register(Resource).to(
            Resource, scope=Scope.SCOPED, dispose=Resource.aclose
        )
        container = builder.build()
        async with container.scope() as scope:
            resource = await scope.resolve(Resource)
            # Bound to locals so each reading is narrowed on its own; comparing the attribute
            # twice would let mypy carry the first result forward and call the second dead.
            during = resource.closed

        after = resource.closed
        assert during is False
        assert after is True
        await container.aclose()

    async def test_every_scope_releases_its_own_instance(self) -> None:
        released: list[int] = []
        builder = ContainerBuilder()
        builder.register(Resource).to(
            Resource,
            scope=Scope.SCOPED,
            dispose=lambda resource: released.append(id(resource)),
        )
        container = builder.build()

        async with container.scope() as first:
            one = await first.resolve(Resource)
        async with container.scope() as second:
            two = await second.resolve(Resource)

        assert one is not two
        assert released == [id(one), id(two)]
        await container.aclose()

    async def test_accepts_a_synchronous_dispose(self) -> None:
        log: list[str] = []
        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(log, "leaf")
        )
        container = builder.build()
        await container.resolve(Leaf)

        await container.aclose()

        assert log == ["leaf"]

    async def test_does_not_dispose_what_was_never_resolved(self) -> None:
        log: list[str] = []
        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(log, "leaf")
        )
        container = builder.build()

        await container.aclose()

        assert log == []

    async def test_a_scope_does_not_dispose_the_root_s_singletons(self) -> None:
        log: list[str] = []
        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(log, "leaf")
        )
        container = builder.build()
        async with container.scope() as scope:
            await scope.resolve(Leaf)

        assert log == [], "the scope released an instance the root owns"

        await container.aclose()
        assert log == ["leaf"]

    async def test_disposes_only_once_however_often_close_is_called(self) -> None:
        log: list[str] = []
        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(log, "leaf")
        )
        container = builder.build()
        await container.resolve(Leaf)

        await container.aclose()
        await container.aclose()

        assert log == ["leaf"]


class TestOrder:
    async def test_releases_dependents_before_their_dependencies(self) -> None:
        log: list[str] = []
        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(log, "leaf")
        )
        builder.register(Middle).to(
            Middle, scope=Scope.SINGLETON, dispose=make_recorder(log, "middle")
        )
        builder.register(Top).to(
            Top, scope=Scope.SINGLETON, dispose=make_recorder(log, "top")
        )
        container = builder.build()
        await container.resolve(Top)

        await container.aclose()

        assert log == ["top", "middle", "leaf"]


class TestFailures:
    async def test_collects_every_failure_rather_than_stopping_at_the_first(
        self,
    ) -> None:
        released: list[str] = []

        def explode(_: Any) -> None:
            raise RuntimeError("release failed")

        builder = ContainerBuilder()
        builder.register(Leaf).to(
            Leaf, scope=Scope.SINGLETON, dispose=make_recorder(released, "leaf")
        )
        builder.register(Middle).to(Middle, scope=Scope.SINGLETON, dispose=explode)
        builder.register(Top).to(Top, scope=Scope.SINGLETON, dispose=explode)
        container = builder.build()
        await container.resolve(Top)

        with pytest.raises(DisposalError) as caught:
            await container.aclose()

        assert len(caught.value.exceptions) == 2
        assert released == ["leaf"], "a failure stopped later instances being released"

    async def test_the_container_is_closed_even_when_disposal_fails(self) -> None:
        def explode(_: Any) -> None:
            raise RuntimeError("release failed")

        builder = ContainerBuilder()
        builder.register(Leaf).to(Leaf, scope=Scope.SINGLETON, dispose=explode)
        container = builder.build()
        await container.resolve(Leaf)

        with pytest.raises(DisposalError):
            await container.aclose()

        with pytest.raises(Exception, match="closed"):
            await container.resolve(Leaf)

    async def test_the_failures_split_with_except_star(self) -> None:
        def explode(_: Any) -> None:
            raise RuntimeError("release failed")

        builder = ContainerBuilder()
        builder.register(Leaf).to(Leaf, scope=Scope.SINGLETON, dispose=explode)
        container = builder.build()
        await container.resolve(Leaf)

        caught: list[str] = []
        try:
            await container.aclose()
        except* RuntimeError as group:
            caught.extend(str(error) for error in group.exceptions)

        assert caught == ["release failed"]


class TestDisposalRunsBeforeClosing:
    async def test_a_dispose_callback_can_still_resolve_from_the_container(
        self,
    ) -> None:
        """A bus draining its in-flight work needs this; closing first would forbid it."""
        resolved: list[Leaf] = []

        async def resolve_during_dispose(_: Any) -> None:
            resolved.append(await container.resolve(Leaf))

        builder = ContainerBuilder()
        builder.register(Leaf).to(Leaf, scope=Scope.SINGLETON)
        builder.register(Middle).to(
            Middle, scope=Scope.SINGLETON, dispose=resolve_during_dispose
        )
        container = builder.build()
        await container.resolve(Middle)

        await container.aclose()

        assert len(resolved) == 1


class TestGuards:
    def test_rejects_a_dispose_on_a_transient_binding(self) -> None:
        """Nothing is kept, so the callback could only ever be a silent no-op."""
        builder = ContainerBuilder()

        with pytest.raises(InvalidRegistrationError, match="cannot be disposed"):
            builder.register(Leaf).to(
                Leaf, scope=Scope.TRANSIENT, dispose=lambda _: None
            )

    def test_rejects_a_dispose_that_is_not_callable(self) -> None:
        builder = ContainerBuilder()
        not_callable: Any = "close"

        with pytest.raises(InvalidRegistrationError, match="not callable"):
            builder.register(Leaf).to(Leaf, scope=Scope.SINGLETON, dispose=not_callable)

    def test_a_registered_instance_takes_no_dispose(self) -> None:
        """The container releases what it created, and it did not create this."""
        builder = ContainerBuilder()
        binder: Any = builder.register(Leaf)

        with pytest.raises(TypeError):
            binder.to_instance(Leaf(), dispose=lambda _: None)

    async def test_a_binding_without_dispose_releases_nothing(self) -> None:
        builder = ContainerBuilder()
        builder.register(Resource).to(Resource, scope=Scope.SINGLETON)
        container = builder.build()
        resource = await container.resolve(Resource)

        await container.aclose()

        assert resource.closed is False
