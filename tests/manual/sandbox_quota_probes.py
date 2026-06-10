"""
C-stage 开工三探针(配额机制设计输入)— 需要本机 Docker daemon,手动跑。

    python tests/manual/sandbox_quota_probes.py [image]

① du 开销:scratch 目录在「正常 / 大量小文件」两档下,os.walk 求和 vs `du -s`
   子进程的耗时 —— 决定 watchdog 的实现方式与节奏(SANDBOX_WATCHDOG_INTERVAL)。
② exec 进行中杀容器:模拟 watchdog 超额 kill,观察 in-flight exec 在当前
   SandboxSession 实现下抛什么(裸 DockerError?stream EOF?)—— 决定错误收口。
③ ReadonlyRootfs + /tmp bind:pandoc(md→docx)/ matplotlib(savefig PNG)在
   只读 rootfs 下是否工作,HOME/缓存写点要不要重定向、重定向到哪。
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import aiodocker  # noqa: E402

from config import config  # noqa: E402

config.SANDBOX_SCRATCH_ROOT = "/tmp/artifactflow-sandbox-probes"
if len(sys.argv) > 1:
    config.SANDBOX_IMAGE = sys.argv[1]

from tools.builtin.sandbox_session import SandboxSession, WORKSPACE_MOUNT  # noqa: E402


# ----------------------------------------------------------------------
# ① du 开销
# ----------------------------------------------------------------------

def _walk_size(root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _du_size_kb(root: str) -> int:
    out = subprocess.run(["du", "-sk", root], capture_output=True, text=True)
    return int(out.stdout.split()[0])


def probe_1_du_cost():
    print("\n=== ① du 开销(walk vs du 子进程)===")
    base = "/tmp/artifactflow-du-probe"
    shutil.rmtree(base, ignore_errors=True)

    scenarios = [
        ("正常工作区: 200 文件 × 64KB", 200, 100, 65536),
        ("小文件轰炸: 50_000 文件 × 100B", 50_000, 500, 100),
    ]
    for label, n_files, n_dirs, size in scenarios:
        root = os.path.join(base, label.split(":")[0].strip())
        os.makedirs(root, exist_ok=True)
        payload = b"x" * size
        t0 = time.monotonic()
        for i in range(n_files):
            d = os.path.join(root, f"d{i % n_dirs}")
            if i < n_dirs:
                os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"f{i}"), "wb") as f:
                f.write(payload)
        gen_s = time.monotonic() - t0

        t0 = time.monotonic()
        walk_bytes = _walk_size(root)
        walk_s = time.monotonic() - t0

        t0 = time.monotonic()
        du_kb = _du_size_kb(root)
        du_s = time.monotonic() - t0

        print(f"  {label}(生成 {gen_s:.1f}s)")
        print(f"    os.walk: {walk_s * 1000:.0f}ms → {walk_bytes / 1024 / 1024:.1f}MB (apparent)")
        print(f"    du -sk : {du_s * 1000:.0f}ms → {du_kb / 1024:.1f}MB (block usage)")
    shutil.rmtree(base, ignore_errors=True)


# ----------------------------------------------------------------------
# ② exec 进行中杀容器
# ----------------------------------------------------------------------

async def probe_2_kill_during_exec():
    print("\n=== ② exec 进行中杀容器(watchdog kill 预演)===")
    suffix = uuid.uuid4().hex[:8]
    session = SandboxSession(f"conv-probe{suffix}", f"msg-probe{suffix}")

    async def killer():
        await asyncio.sleep(3)  # 容器起好、sleep 在跑
        docker = aiodocker.Docker()
        try:
            containers = await docker.containers.list(
                filters={"label": [f"artifactflow.sandbox.message-id={session.message_id}"]}
            )
            assert containers, "没找到目标容器"
            t0 = time.monotonic()
            await containers[0].delete(force=True)
            print(f"  [killer] delete(force=True) 返回,耗时 {time.monotonic() - t0:.1f}s")
        finally:
            await docker.close()

    kill_task = asyncio.create_task(killer())
    t0 = time.monotonic()
    try:
        result = await session.exec("sleep 60")
        print(
            f"  exec 正常返回: exit={result.exit_code} dur={result.duration:.1f}s "
            f"output={result.output!r}"
        )
    except Exception as e:
        print(f"  exec 抛异常: {type(e).__module__}.{type(e).__name__}: {e}")
        print(f"  (耗时 {time.monotonic() - t0:.1f}s)")
    finally:
        await kill_task
        await session.close()

    # 容器死后再 exec(sticky 行为预演:当前实现 _container 句柄还在,会发生什么?)
    try:
        result = await session.exec("echo after-kill")
        print(f"  kill 后再 exec: exit={result.exit_code} output={result.output!r}")
    except Exception as e:
        print(f"  kill 后再 exec 抛: {type(e).__module__}.{type(e).__name__}: {e}")


# ----------------------------------------------------------------------
# ③ ReadonlyRootfs + /tmp bind + 写点重定向
# ----------------------------------------------------------------------

PANDOC_CMD = "echo '# Title' > t.md && pandoc t.md -o t.docx && ls -la t.docx"
MPL_CMD = (
    "python3 -c \""
    "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; "
    "plt.plot([1,2,3]); plt.savefig('p.png'); print('saved', flush=True)\" "
    "&& ls -la p.png"
)
PROBE_CMDS = [
    ("写容器 /tmp", "echo x > /tmp/probe && cat /tmp/probe"),
    ("写 rootfs(应失败)", "touch /usr/local/probe 2>&1; echo rc=$?"),
    ("pandoc md→docx", PANDOC_CMD),
    ("matplotlib savefig", MPL_CMD),
]


async def _run_ro_variant(label: str, env: list, tmp_bind: bool):
    print(f"\n  --- 变体: {label} ---")
    suffix = uuid.uuid4().hex[:8]
    scratch = os.path.join(config.SANDBOX_SCRATCH_ROOT, f"ro-probe-{suffix}")
    ws_dir = os.path.join(scratch, "workspace")
    tmp_dir = os.path.join(scratch, "tmp")
    for d in (ws_dir, tmp_dir):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o777)
    os.chmod(scratch, 0o777)

    binds = [f"{ws_dir}:{WORKSPACE_MOUNT}:rw"]
    if tmp_bind:
        binds.append(f"{tmp_dir}:/tmp:rw")

    docker = aiodocker.Docker()
    container = None
    try:
        container = await docker.containers.create(
            config={
                "Image": config.SANDBOX_IMAGE,
                "Cmd": ["sleep", "infinity"],
                "WorkingDir": WORKSPACE_MOUNT,
                "Env": env,
                "HostConfig": {
                    "Binds": binds,
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                },
            },
            name=f"af-ro-probe-{suffix}",
        )
        await container.start()
        for name, cmd in PROBE_CMDS:
            exec_ = await container.exec(
                ["timeout", "--signal=KILL", "60", "bash", "-c", cmd],
                stdout=True, stderr=True, workdir=WORKSPACE_MOUNT,
            )
            chunks = []
            async with exec_.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    chunks.append(msg.data.decode("utf-8", "replace"))
            info = await exec_.inspect()
            out = "".join(chunks).strip().replace("\n", "\n      ")
            print(f"    [{name}] exit={info.get('ExitCode')}\n      {out}")
    finally:
        if container is not None:
            await container.delete(force=True)
        await docker.close()
        shutil.rmtree(scratch, ignore_errors=True)


async def probe_3_readonly_rootfs():
    print("\n=== ③ ReadonlyRootfs + /tmp bind ===")
    # 变体 A:裸 ReadonlyRootfs,无 /tmp bind、无 env 重定向 —— 看什么坏
    await _run_ro_variant("裸 ReadonlyRootfs(预期 /tmp 不可写)", env=[], tmp_bind=False)
    # 变体 B:/tmp bind 入池,无 env 重定向 —— 看 HOME 缓存写点是否还坏
    await _run_ro_variant("/tmp bind,HOME 不动", env=[], tmp_bind=True)
    # 变体 C:/tmp bind + HOME/缓存全部指进 /tmp —— 候选最终形态
    await _run_ro_variant(
        "/tmp bind + HOME=/tmp/home",
        env=["HOME=/tmp/home", "XDG_CACHE_HOME=/tmp/home/.cache", "MPLCONFIGDIR=/tmp/home/.mpl"],
        tmp_bind=True,
    )


async def main():
    print(f"镜像: {config.SANDBOX_IMAGE}")
    probe_1_du_cost()
    await probe_2_kill_during_exec()
    await probe_3_readonly_rootfs()


if __name__ == "__main__":
    asyncio.run(main())
