"""
沙盒无孤儿矩阵(C-session 切片)— 需要本机 Docker daemon,手动跑。

    python tests/manual/sandbox_no_orphan_matrix.py [image]

镜像默认 config.SANDBOX_IMAGE(artifactflow-sandbox:latest);没构建本地沙盒镜
像时可传 python:3.11-slim(有 bash/timeout/sleep,足够本矩阵)。

矩阵(C 验收标准 ② 的 C-session 子集;SIGKILL worker 条留给 C-reap 的 reaper):
  1. 正常路径        exec → close
  2. while-true      容器内 timeout --signal=KILL 真杀(exit 137,时长≈上限)
  3. 协作/外部取消   exec 进行中 task.cancel(),finally close
  4. 起容器中取消    ensure_container 进行中 task.cancel(),close 收半成品
  5. runner 集成     register_cleanup → _wrapped finally 拆除(成功+取消两条)

每条跑完断言:daemon 上无该 turn label 的容器 + scratch 目录已删(双零残留)。
顺带自验 aiodocker exec multiplexed stream 的 demux(stdout/stderr 分流)。
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aiodocker  # noqa: E402

from config import config  # noqa: E402

# scratch 根目录隔离到本脚本专用路径,免得误碰真实部署的根目录
config.SANDBOX_SCRATCH_ROOT = "/tmp/artifactflow-sandbox-matrix"
if len(sys.argv) > 1:
    config.SANDBOX_IMAGE = sys.argv[1]

from tools.builtin.sandbox_session import (  # noqa: E402
    LABEL_MESSAGE,
    SandboxSession,
)
from api.services.execution_runner import ExecutionRunner  # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def _ids():
    suffix = uuid.uuid4().hex[:8]
    return f"conv-matrix{suffix}", f"msg-matrix{suffix}"


async def assert_no_residue(name: str, session: SandboxSession, note: str = ""):
    """双零残留:daemon 无该 msg label 的容器 + scratch 目录不存在。"""
    docker = aiodocker.Docker()
    try:
        leftovers = await docker.containers.list(
            all=True,
            filters={"label": [f"{LABEL_MESSAGE}={session.message_id}"]},
        )
    finally:
        await docker.close()
    container_ok = len(leftovers) == 0
    dir_ok = not os.path.exists(session.scratch_dir)
    ok = container_ok and dir_ok
    detail = note
    if not container_ok:
        detail += f" 孤儿容器×{len(leftovers)}!"
    if not dir_ok:
        detail += f" scratch 残留: {session.scratch_dir}"
    results.append((name, ok, detail.strip()))
    print(f"  [{PASS if ok else FAIL}] {name} {detail.strip()}")
    # 失败也继续跑剩余矩阵,但把残留收掉避免污染下一条
    if not container_ok:
        d = aiodocker.Docker()
        for c in leftovers:
            await c.delete(force=True)
        await d.close()


async def case_1_normal():
    print("\n=== 1. 正常路径(exec → close;顺带验 demux)===")
    conv, msg = _ids()
    session = SandboxSession(conv, msg)
    try:
        result = await session.exec("echo to-stdout; echo to-stderr >&2; pwd")
        print(f"  exit={result.exit_code} dur={result.duration:.1f}s output={result.output!r}")
        assert result.exit_code == 0, f"exit {result.exit_code}"
        assert "to-stdout" in result.output and "to-stderr" in result.output, "demux 丢流"
        assert "/workspace" in result.output, "workdir 不是 /workspace"
        # workspace 跨调用持久
        await session.exec("echo data > f.txt")
        second = await session.exec("cat f.txt")
        assert "data" in second.output, "workspace 未跨调用持久"
    finally:
        await session.close()
    await assert_no_residue("正常路径", session)


async def case_2_while_true():
    print("\n=== 2. while-true(容器内 timeout 真杀)===")
    old = config.SANDBOX_COMMAND_TIMEOUT
    config.SANDBOX_COMMAND_TIMEOUT = 3
    conv, msg = _ids()
    session = SandboxSession(conv, msg)
    try:
        result = await session.exec("while true; do :; done")
        print(f"  exit={result.exit_code} dur={result.duration:.1f}s")
        assert result.exit_code == 137, f"期望 137(SIGKILL),得到 {result.exit_code}"
        assert result.duration < 10, f"杀晚了: {result.duration:.1f}s"
    finally:
        config.SANDBOX_COMMAND_TIMEOUT = old
        await session.close()
    await assert_no_residue("while-true 真杀", session, note=f"(exit 137, {result.duration:.1f}s)")


async def case_3_cancel_mid_exec():
    print("\n=== 3. exec 进行中取消 ===")
    conv, msg = _ids()
    session = SandboxSession(conv, msg)

    async def run():
        try:
            await session.exec("sleep 60")
        finally:
            await session.close()

    task = asyncio.create_task(run())
    await asyncio.sleep(3)  # 容器起好、命令在跑
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await assert_no_residue("exec 中取消", session)


async def case_4_cancel_mid_start():
    print("\n=== 4. 起容器途中取消 ===")
    conv, msg = _ids()
    session = SandboxSession(conv, msg)

    async def run():
        try:
            await session.exec("echo hi")
        finally:
            await session.close()

    task = asyncio.create_task(run())
    await asyncio.sleep(0.3)  # create/start 在飞
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await assert_no_residue("起容器中取消", session)


class _NullTransport:
    async def create_stream(self, *a, **k): pass
    async def close_stream(self, *a): return True
    async def push_event(self, *a): return True


async def case_5_runner_integrated():
    print("\n=== 5. runner 集成(register_cleanup → _wrapped finally)===")
    runner = ExecutionRunner(max_concurrent=2)

    # 5a. 成功路径
    conv, msg = _ids()
    session = SandboxSession(conv, msg)
    runner.register_cleanup(msg, session.close)

    async def good_turn():
        result = await session.exec("echo runner-path")
        assert "runner-path" in result.output

    task = await runner.submit(conv, msg, good_turn, user_id="u1", stream_transport=_NullTransport())
    await asyncio.gather(task, return_exceptions=True)
    await assert_no_residue("runner 成功路径", session)

    # 5b. 外部取消
    conv, msg = _ids()
    session = SandboxSession(conv, msg)
    runner.register_cleanup(msg, session.close)

    async def long_turn():
        await session.exec("sleep 60")

    task = await runner.submit(conv, msg, long_turn, user_id="u1", stream_transport=_NullTransport())
    await asyncio.sleep(3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await assert_no_residue("runner 外部取消", session)


async def main():
    print(f"镜像: {config.SANDBOX_IMAGE}")
    print(f"scratch 根: {config.SANDBOX_SCRATCH_ROOT}")

    await case_1_normal()
    await case_2_while_true()
    await case_3_cancel_mid_exec()
    await case_4_cancel_mid_start()
    await case_5_runner_integrated()

    print("\n" + "=" * 50)
    failed = [name for name, ok, _ in results if not ok]
    for name, ok, detail in results:
        print(f"  [{PASS if ok else FAIL}] {name} {detail}")
    if failed:
        print(f"\n{len(failed)} 条失败: {failed}")
        sys.exit(1)
    print(f"\n全部 {len(results)} 条通过,双零残留 ✓")


if __name__ == "__main__":
    asyncio.run(main())
