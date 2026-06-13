"""DooD compose 接线冒烟 — 在【容器化 backend 内】直接驱动 SandboxSession。

验证 docker-compose.sandbox.yml overlay 的两个接线点,不依赖 LLM:
  1. backend 容器经挂入的 /var/run/docker.sock 能创建沙盒兄弟容器;
  2. scratch 根「宿主↔backend 容器同路径」bind 成立——backend 把 workspace_dir
     作为 bind source 传给 daemon、daemon 按宿主路径解析,若路径不同一,容器内
     写的文件不会出现在 backend 看到的 scratch 目录里。

用法(compose 栈已 up;tests/ 被 .dockerignore 排除在镜像外,故经 stdin 喂入):
  docker compose -f deploy/docker-compose.intranet.yml -f deploy/docker-compose.sandbox.yml \
    exec -T backend python - < tests/manual/dood_compose_smoke.py

期望输出以 "DOOD SMOKE: ALL PASS" 结尾;任何一步失败即非零退出。
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/src")

from tools.builtin.sandbox_session import SandboxSession  # noqa: E402


async def main() -> int:
    conv = f"smoke-{uuid.uuid4().hex[:8]}"
    msg = uuid.uuid4().hex[:8]
    session = SandboxSession(conv, msg)
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + (f" ({detail})" if detail else ""))
        if not ok:
            failures.append(name)

    try:
        # 1. sock 通路:创建+启动沙盒容器
        await session.ensure_container()
        check("create sandbox container via mounted docker.sock", session.started)

        # 2. 容器内执行 + 输出回传
        r = await session.exec("echo dood-ok && id -u")
        check(
            "exec inside sandbox",
            r.exit_code == 0 and "dood-ok" in r.output,
            f"exit={r.exit_code}",
        )
        check("runs as uid 1000", "1000" in r.output, r.output.strip().splitlines()[-1])

        # 3. 路径同一律:容器写 /workspace,backend 侧 scratch 目录读得到
        token = uuid.uuid4().hex
        r = await session.exec(f"echo {token} > /workspace/probe.txt")
        host_path = os.path.join(session.workspace_dir, "probe.txt")
        content = ""
        if os.path.exists(host_path):
            with open(host_path) as f:
                content = f.read().strip()
        check(
            "path-identity: container write visible at backend-side scratch path",
            content == token,
            host_path,
        )
    finally:
        await session.close()

    # 4. 拆除:容器与 scratch 目录无残留
    check("scratch dir removed on close", not os.path.exists(session.scratch_dir))

    if failures:
        print(f"DOOD SMOKE: {len(failures)} FAILED: {failures}")
        return 1
    print("DOOD SMOKE: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
