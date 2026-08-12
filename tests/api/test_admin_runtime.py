"""Public contract for the admin runtime diagnostics snapshot."""

import asyncio

from httpx import AsyncClient

import api.dependencies as deps
from api.dependencies import (
    get_runtime_status_reader,
    get_runtime_store,
    get_task_supervisor,
)
from core.task_supervisor import TaskScope, TaskSupervisor
from observability import admin_runtime
from utils.instance import INSTANCE_ID


class _Sampler:
    def latest_snapshot(self):
        return {"loop_lag_ms": 12.5, "pool_checked_out": 2}


async def test_admin_runtime_reports_process_tasks_and_shared_leases(
    app,
    admin_client: AsyncClient,
):
    store = app.dependency_overrides[get_runtime_store]()
    supervisor: TaskSupervisor = app.dependency_overrides[get_task_supervisor]()
    status_reader = app.dependency_overrides[get_runtime_status_reader]()
    blocker = asyncio.Event()
    started = asyncio.Event()

    assert await store.try_acquire_lease("conv-runtime", "msg-runtime") is None

    def workload_factory(scope: TaskScope):
        scope.add_cleanup(
            "lease",
            lambda: store.release_lease("conv-runtime", "msg-runtime"),
        )

        async def workload():
            started.set()
            await blocker.wait()
        return workload

    old_supervisor = deps._task_supervisor
    old_status_reader = deps._runtime_status_reader
    old_sampler = admin_runtime.get_sampler()
    deps._task_supervisor = supervisor
    deps._runtime_status_reader = status_reader
    admin_runtime.set_sampler(_Sampler())
    try:
        await supervisor.submit("msg-runtime", workload_factory)
        await started.wait()

        response = await admin_client.get("/api/v1/admin/runtime")

        assert response.status_code == 200
        body = response.json()
        assert body["instance_id"] == INSTANCE_ID
        assert body["sampler"] == {
            "loop_lag_ms": 12.5,
            "pool_checked_out": 2,
        }
        assert body["active_conversations"] == ["conv-runtime"]
        assert body["active_tasks"] == 1
        assert body["ts"]
    finally:
        blocker.set()
        await supervisor.shutdown(timeout=2)
        deps._task_supervisor = old_supervisor
        deps._runtime_status_reader = old_status_reader
        admin_runtime.set_sampler(old_sampler)
