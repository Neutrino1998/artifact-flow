"""Public contract for the admin runtime diagnostics snapshot."""

import asyncio

from httpx import AsyncClient

import api.dependencies as deps
from api.dependencies import get_execution_runner, get_stream_transport
from api.services.execution_runner import ExecutionRunner
from observability import admin_runtime
from utils.instance import INSTANCE_ID


class _Sampler:
    def latest_snapshot(self):
        return {"loop_lag_ms": 12.5, "pool_checked_out": 2}


async def test_admin_runtime_reports_process_tasks_and_shared_leases(
    app,
    admin_client: AsyncClient,
):
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
    transport = app.dependency_overrides[get_stream_transport]()
    blocker = asyncio.Event()
    started = asyncio.Event()

    async def workload():
        started.set()
        await blocker.wait()

    old_runner = deps._execution_runner
    old_sampler = admin_runtime.get_sampler()
    deps._execution_runner = runner
    admin_runtime.set_sampler(_Sampler())
    try:
        await runner.submit(
            "conv-runtime",
            "msg-runtime",
            workload,
            user_id="runtime-user",
            stream_transport=transport,
        )
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
        await runner.shutdown(timeout=2)
        deps._execution_runner = old_runner
        admin_runtime.set_sampler(old_sampler)
