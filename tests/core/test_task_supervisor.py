"""Generic TaskSupervisor contracts; no Conversation or transport fixtures."""

import asyncio

import pytest

from core.task_supervisor import (
    DuplicateTaskError,
    TaskQueued,
    TaskScope,
    TaskSupervisor,
)


async def test_runs_workload_and_cleans_registry():
    supervisor = TaskSupervisor(max_concurrent=2)
    ran = asyncio.Event()

    def factory(scope: TaskScope):
        async def workload():
            ran.set()
        return workload

    task = await supervisor.submit("task-1", factory)
    await task
    assert ran.is_set()
    assert supervisor.active_task_count == 0


async def test_duplicate_task_id_is_rejected():
    supervisor = TaskSupervisor()
    blocker = asyncio.Event()

    def factory(scope: TaskScope):
        async def workload():
            await blocker.wait()
        return workload

    await supervisor.submit("task-1", factory)
    with pytest.raises(DuplicateTaskError):
        await supervisor.submit("task-1", factory)
    blocker.set()
    await supervisor.shutdown(timeout=1)


async def test_capacity_gate_and_generic_queued_event():
    supervisor = TaskSupervisor(max_concurrent=1)
    first_blocker = asyncio.Event()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    events: list[TaskQueued] = []

    def first(scope: TaskScope):
        async def workload():
            first_started.set()
            await first_blocker.wait()
        return workload

    def second(scope: TaskScope):
        async def workload():
            second_started.set()
        return workload

    async def record(event: TaskQueued):
        events.append(event)

    await supervisor.submit("first", first)
    await first_started.wait()
    second_task = await supervisor.submit(
        "second", second, event_sink=record
    )
    await asyncio.sleep(0)
    assert not second_started.is_set()
    assert events == [TaskQueued(ahead=0, capacity=1)]
    first_blocker.set()
    await second_task
    await supervisor.shutdown(timeout=1)


async def test_lifo_cleanup_is_bounded_and_continues_after_failure():
    supervisor = TaskSupervisor(max_concurrent=1, cleanup_timeout=0.01)
    order: list[str] = []

    async def first():
        order.append("first")

    async def failing():
        order.append("failing")
        raise RuntimeError("boom")

    async def hanging():
        order.append("hanging")
        await asyncio.Event().wait()

    def factory(scope: TaskScope):
        scope.add_cleanup("first", first)
        scope.add_cleanup("failing", failing)
        scope.add_cleanup("hanging", hanging)

        async def workload():
            return None
        return workload

    task = await supervisor.submit("task-1", factory)
    await task
    assert order == ["hanging", "failing", "first"]


async def test_cancel_and_shutdown_run_cleanup():
    supervisor = TaskSupervisor()
    blocker = asyncio.Event()
    cleaned = asyncio.Event()

    def factory(scope: TaskScope):
        async def cleanup():
            cleaned.set()
        scope.add_cleanup("resource", cleanup)

        async def workload():
            await blocker.wait()
        return workload

    task = await supervisor.submit("task-1", factory)
    assert supervisor.cancel("task-1") is True
    await task
    assert cleaned.is_set()
    assert supervisor.active_task_count == 0


async def test_cancel_while_queued_never_starts_workload_and_runs_cleanup():
    supervisor = TaskSupervisor(max_concurrent=1)
    first_started = asyncio.Event()
    hold_first = asyncio.Event()
    second_started = asyncio.Event()
    second_cleaned = asyncio.Event()

    def first(scope: TaskScope):
        async def workload():
            first_started.set()
            await hold_first.wait()
        return workload

    def second(scope: TaskScope):
        async def cleanup():
            second_cleaned.set()
        scope.add_cleanup("second resource", cleanup)

        async def workload():
            second_started.set()
        return workload

    await supervisor.submit("first", first)
    await first_started.wait()
    queued = await supervisor.submit("second", second)
    assert supervisor.cancel("second") is True
    await queued

    assert not second_started.is_set()
    assert second_cleaned.is_set()
    hold_first.set()
    await supervisor.shutdown(timeout=1)


async def test_non_conversation_workload_and_long_running_observation(monkeypatch):
    supervisor = TaskSupervisor()
    blocker = asyncio.Event()

    def factory(scope: TaskScope):
        async def workload():
            await blocker.wait()
        return workload

    await supervisor.submit("plain-work", factory)
    supervisor._task_started_at["plain-work"] -= 5
    assert supervisor.long_running_count(1) == 1
    blocker.set()
    await supervisor.shutdown(timeout=1)
