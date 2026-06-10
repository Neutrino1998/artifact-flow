"""
SandboxReaper — lease-anchored 孤儿沙盒回收(C 阶段二级兜底)

正常路径下,每条退出(成功/超时/协作取消/外部取消)都经 execution_runner._wrapped
的真 finally → SandboxSession.close() 拆容器 + 删 scratch。reaper 兜的是 finally
**不执行**的那条:worker 被 SIGKILL / OOM 杀,容器归 daemon(DooD)不随 worker 死,
孤儿继续烧 CPU(2026-05-14 同型失效)。

**lease 是唯一 liveness 真相源** —— 它恰在"turn 合法地在活 worker 上跑"期间被持续
续租,正是容器/scratch 应当存在的充要条件。reaper 不猜固定余量、零误杀:

  孤儿 = 资源侧枚举(① daemon 上带本命名空间 label 的容器 ② scratch 根的直属
         {conv}__{msg} 目录)− `list_active_executions` 的活跃 (conv, msg) 集。

两条纪律(均来自 plan 锁定 + 前序 review 外推):
  - **对账粒度 per-turn**:活跃谓词是 `active.get(conv) == msg`,不是"conv 有没有活跃
    turn"。否则同会话紧接的新 turn 持活 lease,会让上一 turn 漏拆的孤儿被误判有主、
    永不回收。msg id 全局唯一,(conv,msg) 配对判定最稳。
  - **scratch 根枚举走 sandbox_fs.list_dir(fd 钉住、单层不递归)**:活跃容器能把自己
    workspace 内的子目录换成池外链,按名字递归会跟链遍历宿主(watchdog 第 3 轮母题)。
    reaper 只需根目录直属名做差集,够不着这个洞。

**grace**:只回收存活 > SANDBOX_REAP_GRACE_SEC 的资源。资源恒在 lease 之后创建
(lease 在 _wrapped 入口取得,容器 lazy 于其后的工具调用),故"资源在、lease 不在"
通常意味 turn 已结束;grace 仅为躲开 Redis 副本/scan 的可见性差一拍,不是 liveness 依据。

**多 worker**:每个 worker 各跑一个 reaper,共享同一 daemon + Redis 活跃集。重复回收
同一孤儿幂等(容器 404 / 目录 FileNotFoundError 都容忍)。这也正是"独占 daemon 的
worker 死不重启"残留洞的缓解 —— 有 sibling worker 就能扫到。

**编排器可换性**:与 SandboxSession 一样,aiodocker 调用收口在本类。将来上 k8s 换成
按 label 列 Pod + 删 Pod,差集谓词与 scratch 源不变。
"""

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

import aiodocker
from aiodocker.exceptions import DockerError

from config import config
from tools.builtin import sandbox_fs
from tools.builtin.sandbox_session import (
    LABEL_CONVERSATION,
    LABEL_MESSAGE,
    LABEL_NAMESPACE,
    SANDBOX_LABEL,
    parse_scratch_dir_name,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@dataclass
class ReapStats:
    """一次扫描的结果(供日志聚合 + 测试断言)。"""
    containers_seen: int = 0
    dirs_seen: int = 0
    containers_reaped: int = 0
    dirs_reaped: int = 0
    skipped_young: int = 0          # 在 grace 内、本轮不动
    reaped: List[str] = field(default_factory=list)  # "container <id> (conv/msg)" / "dir <name>"


class SandboxReaper:
    """周期 + 启动扫的孤儿回收器。生命周期跨 lifespan,在 main 的 lifespan 起停。"""

    def __init__(
        self,
        store,
        *,
        scratch_root: Optional[str] = None,
        namespace: Optional[str] = None,
        interval_sec: Optional[int] = None,
        grace_sec: Optional[int] = None,
        docker_factory: Optional[Callable[[], "aiodocker.Docker"]] = None,
    ):
        self._store = store
        self._scratch_root = scratch_root or config.SANDBOX_SCRATCH_ROOT
        self._namespace = namespace or (config.REDIS_KEY_PREFIX or "default")
        self._interval = interval_sec if interval_sec is not None else config.SANDBOX_REAP_INTERVAL_SEC
        self._grace = grace_sec if grace_sec is not None else config.SANDBOX_REAP_GRACE_SEC
        self._docker_factory = docker_factory or aiodocker.Docker
        self._docker: Optional["aiodocker.Docker"] = None
        self._task: Optional[asyncio.Task] = None
        self._consecutive_failures = 0  # tick 级失败去重(daemon 不可达时不每跳刷屏)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is not None:
            return
        self._docker = self._docker_factory()
        self._task = asyncio.create_task(self._run(), name="sandbox-reaper")
        logger.info(
            f"Sandbox reaper started (namespace={self._namespace}, "
            f"interval={self._interval}s, grace={self._grace}s)"
        )

    async def _stop_loop(self) -> None:
        """只停周期 task,保留 docker client(final_sweep 还要用)。幂等。"""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Sandbox reaper task teardown raised")

    async def stop(self) -> None:
        await self._stop_loop()
        docker, self._docker = self._docker, None
        if docker is not None:
            try:
                await docker.close()
            except Exception:
                logger.exception("Sandbox reaper aiodocker client close failed")

    async def final_sweep(self) -> "ReapStats":
        """停机最后一扫(main lifespan 在 runner.shutdown 之后、close 之前调)。

        兜住 shutdown 期间 SandboxSession.close() 超时/失败漏下的孤儿 —— 单副本停机后
        不会再有 reaper 收尾,这些孤儿会一直跑到下次启动(P2)。先停周期 task 免与本扫
        并发,docker client 留到随后的 stop() 关。

        **grace 策略按 store 分**:进程本地 store(单进程契约,此刻 runner 已 shutdown =
        无任何在途 turn)忽略 grace,新鲜残留也收;共享 store(多 worker)保留 grace ——
        兄弟进程可能正起新 turn,grace=0 会把它没来得及写 lease 的容器误删。
        """
        await self._stop_loop()
        ignore_grace = not getattr(self._store, "is_shared", False)
        return await self.reap_once(ignore_grace=ignore_grace)

    async def _run(self) -> None:
        """启动立即扫一次,之后每 interval 扫一次。单跳异常不杀循环。"""
        while True:
            try:
                await self.reap_once()
                if self._consecutive_failures:
                    logger.info("Sandbox reaper recovered after "
                                f"{self._consecutive_failures} failed tick(s)")
                    self._consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._consecutive_failures += 1
                # daemon 不可达等环境问题会持续:首跳 + 每 10 跳记一次,免刷屏。
                # 单跳失败不致命(下跳重试),故 warning 非 error。
                if self._consecutive_failures == 1 or self._consecutive_failures % 10 == 0:
                    logger.warning(
                        f"Sandbox reaper tick failed (x{self._consecutive_failures}): {e}"
                    )
            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------

    async def reap_once(self, *, ignore_grace: bool = False) -> ReapStats:
        """一次完整对账:枚举资源 → 读活跃集 → 删孤儿。返回统计。

        活跃集**在枚举之后**读:让 mask 尽量贴近回收决策时刻;且 grace 已挡住
        "枚举期间新建的资源"(其年龄 < grace),两重保险。

        ignore_grace 仅供 final_sweep 在进程本地 store(确无在途 turn)下用,周期扫
        恒为 False。
        """
        stats = ReapStats()
        now = time.time()

        containers = await self._list_sandbox_containers()
        dir_entries = sandbox_fs.list_dir(self._scratch_root)

        active = await self._store.list_active_executions()  # {conv_id: msg_id}

        stats.containers_seen = len(containers)

        # ① 容器:先回收(杀掉可能还在写 scratch 的僵尸),再处置目录
        for container, conv, msg, created in containers:
            if self._is_active(active, conv, msg):
                continue
            if not ignore_grace and now - created <= self._grace:
                stats.skipped_young += 1
                continue
            if await self._delete_container(container, conv, msg):
                stats.containers_reaped += 1
                stats.reaped.append(f"container {container._id[:12]} ({conv}/{msg})")

        # ② scratch 根直属目录(第二源:容器没了目录还在,或 mkdir 后未及起容器即被杀)
        for name, is_dir, mtime in dir_entries:
            if not is_dir:
                continue
            parsed = parse_scratch_dir_name(name)
            if parsed is None:
                continue
            conv, msg = parsed
            stats.dirs_seen += 1
            if self._is_active(active, conv, msg):
                continue
            if not ignore_grace and now - mtime <= self._grace:
                stats.skipped_young += 1
                continue
            if await self._remove_scratch_dir(name):
                stats.dirs_reaped += 1
                stats.reaped.append(f"dir {name}")

        if stats.containers_reaped or stats.dirs_reaped:
            # 回收到孤儿 = 某条退出路径漏拆(worker 死 / close 失败),ops 应知道 → warning
            logger.warning(
                f"Sandbox reaper collected {stats.containers_reaped} orphan container(s) "
                f"+ {stats.dirs_reaped} scratch dir(s): {', '.join(stats.reaped)}"
            )
        return stats

    @staticmethod
    def _is_active(active: Dict[str, str], conv: Optional[str], msg: Optional[str]) -> bool:
        """per-turn 活跃谓词:这个 (conv, msg) 还持着 lease 吗。conv/msg 任一缺失
        (label 残缺的非常规容器)按"不活跃"处置 —— 它本就不该被我们认领。"""
        if not conv or not msg:
            return False
        return active.get(conv) == msg

    async def _list_sandbox_containers(self) -> List[Tuple[object, Optional[str], Optional[str], float]]:
        """列本命名空间的沙盒容器(含已停止)→ [(container, conv, msg, created_unix)]。

        filters 必须 JSON 编码(Docker API 契约);namespace label 把共用 daemon 的
        其他部署挡在外面。
        """
        filters = json.dumps({
            "label": [
                f"{SANDBOX_LABEL}=1",
                f"{LABEL_NAMESPACE}={self._namespace}",
            ]
        })
        containers = await self._docker.containers.list(all=True, filters=filters)
        out: List[Tuple[object, Optional[str], Optional[str], float]] = []
        for c in containers:
            labels = c["Labels"] if "Labels" in c._container else {}
            labels = labels or {}
            # 防御纵深:daemon 端 filters 已按 namespace 过滤,但误杀别的部署的**活**容器
            # 后果严重(跨部署),label 就在手边、零成本再核一遍 —— 谁过滤错了都兜得住。
            if labels.get(LABEL_NAMESPACE) != self._namespace:
                continue
            created = float(c["Created"]) if "Created" in c._container else 0.0
            out.append((c, labels.get(LABEL_CONVERSATION), labels.get(LABEL_MESSAGE), created))
        return out

    async def _delete_container(self, container, conv: Optional[str], msg: Optional[str]) -> bool:
        try:
            await container.delete(force=True)
            return True
        except DockerError as e:
            if e.status == 404:
                return True  # 已被别的 worker / close() 删掉 —— 幂等成功
            logger.error(
                f"Sandbox reaper failed to delete orphan container {container._id[:12]} "
                f"({conv}/{msg}, status={e.status}); will retry next tick"
            )
            return False
        except Exception:
            logger.exception(
                f"Sandbox reaper failed to delete orphan container {container._id[:12]}"
            )
            return False

    async def _remove_scratch_dir(self, name: str) -> bool:
        # 按名 rmtree 安全:scratch 根只有 backend 写(容器只 bind 到 workspace/ 与 tmp/,
        # 够不着父目录),根直属条目不会被容器换成链;list_dir 已 lstat 确认是真目录。
        path = f"{self._scratch_root}/{name}"
        try:
            await asyncio.to_thread(shutil.rmtree, path)
            return True
        except FileNotFoundError:
            return True  # close() / 别的 worker 已删 —— 幂等成功
        except Exception:
            logger.exception(f"Sandbox reaper failed to remove orphan scratch dir {path}")
            return False
