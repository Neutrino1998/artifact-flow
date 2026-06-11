"""
SandboxSession — per-turn 沙盒容器生命周期(C 阶段)

一个 turn 一个 session 对象壳:在 controller_factory 创建(同 ArtifactService,
构造注入沙盒工具),**容器 lazy 于首个沙盒工具调用** —— 多数 turn 不开沙盒,
eager 等于在多数 turn 上空转创建+销毁。拆除挂 execution_runner._wrapped 的
真 finally(cleanup_execution 旁,经 register_cleanup 注册),与 lease 同生灭。

所有 aiodocker 调用收口在本类这一个 seam 后(编排器可换性:将来 Docker↔k8s
只换该层,引擎无感)。容器创建参数(镜像/挂载/runtime/配额)全部来自代码侧
config,绝不可被模型生成内容污染 —— backend 持 docker.sock 等于 host root。

per-command 超时 = 容器内 `timeout --signal=KILL` 包 argv:exec API 收 argv
数组,cmd 整体是一个 argv 元素,无宿主侧 shell、无引号问题,且是**真杀进程**。
tool 侧的 asyncio 超时只是弃等(进程不死,2026-05-14 同型),残留进程由 turn 末
拆容器兜底。
"""

import asyncio
import codecs
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import aiodocker
from aiodocker.exceptions import DockerError

from config import config
from tools.builtin import sandbox_fs
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# 容器内工作区挂载点。固定值非 operator 旋钮:工具描述/mount 返回值都引用它,
# 改路径要连动提示文案,不是配置能独立换的。
WORKSPACE_MOUNT = "/workspace"

# 容器/scratch 目录的归属标识。reaper(C-reap)按 SANDBOX_LABEL 枚举 daemon 上
# 的活容器,再按 conv/msg label 与 list_active_executions 做 per-turn 差集;
# namespace label 隔离共用同一 daemon 的多套部署(各自的 reaper 只认本命名空间)。
SANDBOX_LABEL = "artifactflow.sandbox"
LABEL_NAMESPACE = f"{SANDBOX_LABEL}.namespace"
LABEL_CONVERSATION = f"{SANDBOX_LABEL}.conversation-id"
LABEL_MESSAGE = f"{SANDBOX_LABEL}.message-id"
LABEL_WORKER = f"{SANDBOX_LABEL}.worker-id"

# 本进程(worker/副本)代次唯一标识,import 时生成一次。每个容器/scratch 目录都打上它,
# reaper 的停机 final_sweep 据此**只无 grace 回收本进程自己的**资源(我的 turn 此刻都已
# shutdown 完 = 必是孤儿),别人的留给 grace —— 与副本数无关地正确,不靠 lease 时序论证。
WORKER_ID = uuid.uuid4().hex

# tool 侧 asyncio 弃等护栏 = 命令超时 + 此余量。正常路径由容器内 timeout 在
# SANDBOX_COMMAND_TIMEOUT 处收口;护栏只兜 daemon/exec 通道卡死。
EXEC_ABANDON_GRACE_SEC = 30

# ExitCode 在 stream EOF 后可能短暂为 None(daemon 异步落账),有界轮询。
_EXIT_CODE_POLLS = 20
_EXIT_CODE_POLL_INTERVAL = 0.05


def scratch_dir_name(conversation_id: str, message_id: str, worker_id: str = WORKER_ID) -> str:
    """scratch 子目录名 —— reaper 的第二枚举源按此格式反解归属。

    `{conv}__{msg}__{worker}`:前两段供 per-turn 活跃集差集,第三段(worker-id)供
    final_sweep 判定"是不是本进程的"。conv-* / msg-* id 内部无双下划线、worker 是
    hex,"__" 分隔三段无歧义。
    """
    return f"{conversation_id}__{message_id}__{worker_id}"


def parse_scratch_dir_name(name: str) -> Optional[tuple]:
    """`scratch_dir_name` 的逆 → (conv, msg, worker) 或 None。

    恰好三段(两个 "__" 分隔)且段段非空才算本系统的 scratch 目录;其余返回 None,
    reaper 跳过(不是我们建的,不碰)。
    """
    parts = name.split("__")
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


class SandboxError(Exception):
    """沙盒错误基类(工具层 catch 它转 loud-fail ToolResult)。"""


class SandboxUnavailableError(SandboxError):
    """容器创建失败 / session 已关闭 —— 该 turn 沙盒工具不可用。"""


class SandboxExecTimeoutError(SandboxError):
    """asyncio 弃等护栏触发(exec 通道无响应,超出容器内 timeout + grace)。"""


@dataclass
class SandboxExecResult:
    """单条命令的执行结果(stdout/stderr 按到达序合流)。"""
    exit_code: int
    output: str
    truncated: bool
    duration: float


class SandboxSession:
    """per-turn 沙盒容器壳。

    壳本身零成本;首个 exec 才 lazy 起容器。引擎对单 turn 内的工具调用是串行
    执行(见 docs/architecture/engine.md),故无并发起容器问题,不加锁。

    close() 幂等,且每步独立 best-effort(容器 → scratch → client):任一步失败
    记日志继续,残留由 lease-anchored reaper(C-reap)兜底。
    """

    def __init__(
        self,
        conversation_id: str,
        message_id: str,
        docker_factory: Optional[Callable[[], "aiodocker.Docker"]] = None,
    ):
        self.conversation_id = conversation_id
        self.message_id = message_id
        # 测试注入假 client 的 seam;生产永远走 aiodocker.Docker()(unix socket / DOCKER_HOST)
        self._docker_factory = docker_factory or aiodocker.Docker
        self._docker: Optional["aiodocker.Docker"] = None
        self._container = None
        self._closed = False
        # sticky 失败通道:创建失败 / 准入水位拒绝 / watchdog 超额杀,本 turn 不重试
        # (loud-fail 一次,后续调用立即复述原因)。失败原因多为环境性(镜像缺失 /
        # daemon 不可达 / 池子满),turn 内重试只会重复烧启动超时或重蹈超额。
        self._sticky_failure: Optional[str] = None
        self._scratch_dir = os.path.join(
            config.SANDBOX_SCRATCH_ROOT,
            scratch_dir_name(conversation_id, message_id),
        )
        self._scratch_created = False
        self._watchdog_task: Optional[asyncio.Task] = None

    @property
    def started(self) -> bool:
        return self._container is not None

    @property
    def sticky_failure(self) -> Optional[str]:
        """本 turn 已记录的沙盒不可用原因(创建失败 / 准入拒绝 / 超额杀 /
        容器中途死),None = 无。供不触发 ensure_container 的工具(persist)在
        其前置检查里复述配额失败,与 bash/mount 的 sticky 行为一致(P3)。"""
        return self._sticky_failure

    @property
    def scratch_dir(self) -> str:
        return self._scratch_dir

    @property
    def workspace_dir(self) -> str:
        """宿主侧工作区目录(容器内 /workspace 的 bind 源)。

        mount 在此物化 artifact、persist 从此读回 —— host 直写直读,不走
        docker cp/exec(C′ 锁定 staging 机制不变的理由之一)。
        """
        return os.path.join(self._scratch_dir, "workspace")

    @property
    def tmp_dir(self) -> str:
        """宿主侧 /tmp bind 源:堵 rootfs overlay upper 的无界写洞 —— ReadonlyRootfs
        下容器所有可写路径(/workspace、/tmp、HOME=/tmp/home)全落本 turn scratch,
        统一进池子、统一受 watchdog 计量。"""
        return os.path.join(self._scratch_dir, "tmp")

    # ------------------------------------------------------------------
    # 容器生命周期
    # ------------------------------------------------------------------

    def _container_config(self) -> dict:
        mem_bytes = config.SANDBOX_MEM_LIMIT_MB * 1024 * 1024
        host_config = {
            "Binds": [
                f"{self.workspace_dir}:{WORKSPACE_MOUNT}:rw",
                # /tmp 入池:ReadonlyRootfs 堵死 overlay upper(容器 /tmp 本会写
                # 宿主 /var/lib/docker,无界),可写路径全部显式 bind 进本 turn
                # scratch → 统一受 loop 池子硬墙 + watchdog 软配额管辖。
                f"{self.tmp_dir}:/tmp:rw",
            ],
            "NetworkMode": "none",                # 原则 7:默认全禁网
            "ReadonlyRootfs": True,
            "Memory": mem_bytes,
            "MemorySwap": mem_bytes,              # 同值 = 禁 swap
            "NanoCpus": int(config.SANDBOX_CPU_LIMIT * 1_000_000_000),
            "PidsLimit": config.SANDBOX_PIDS_LIMIT,
            "AutoRemove": False,                  # 删除由 close()/reaper 显式负责
        }
        if config.SANDBOX_RUNTIME:
            host_config["Runtime"] = config.SANDBOX_RUNTIME
        return {
            "Image": config.SANDBOX_IMAGE,
            # 常驻待 exec;镜像默认 CMD 是裸 python3 REPL,显式覆盖
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": WORKSPACE_MOUNT,
            # HOME/缓存写点重定向进 /tmp(镜像 HOME=/home/sandbox 在只读 rootfs 下
            # 不可写)。探针③:matplotlib 无此重定向会降级到逐次建临时缓存目录+警告,
            # 设 MPLCONFIGDIR/XDG_CACHE_HOME 后全绿;pandoc 本就不依赖 HOME。
            "Env": [
                "HOME=/tmp/home",
                "XDG_CACHE_HOME=/tmp/home/.cache",
                "MPLCONFIGDIR=/tmp/home/.mpl",
            ],
            "Labels": {
                SANDBOX_LABEL: "1",
                LABEL_NAMESPACE: config.REDIS_KEY_PREFIX or "default",
                LABEL_CONVERSATION: self.conversation_id,
                LABEL_MESSAGE: self.message_id,
                LABEL_WORKER: WORKER_ID,
            },
            "HostConfig": host_config,
        }

    def _prepare_scratch_dir(self) -> None:
        # 容器内 uid 1000(sandbox)要可写,backend 进程 uid 不定 → 0o777。
        # makedirs 的 mode 被 umask 掩掉,必须显式 chmod。真实 Linux 上的属主/
        # 权限策略是 D 阶段验收项(本机 Docker Desktop 感知不到 uid 错配)。
        # tmp/home 预建:HOME 重定向指向它,部分工具不自建 HOME 目录。
        for d in (
            self._scratch_dir,
            self.workspace_dir,
            self.tmp_dir,
            os.path.join(self.tmp_dir, "home"),
        ):
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777)
        self._scratch_created = True

    def _check_pool_admission(self) -> None:
        """起容器准入水位:scratch 根所在 fs(prod=loop 池子)剩余空间低于阈值时
        拒绝新沙盒(O(1) statvfs)。已在跑的 turn 不受影响 —— 软配额归 watchdog。"""
        st = os.statvfs(config.SANDBOX_SCRATCH_ROOT)
        free_bytes = st.f_bavail * st.f_frsize
        min_free = config.SANDBOX_POOL_MIN_FREE_MB * 1024 * 1024
        if free_bytes < min_free:
            # 容量问题 ops 必须看到,但属预期内防护(非故障)→ warning
            logger.warning(
                f"Sandbox pool low: {free_bytes / 1024 / 1024:.0f}MB free at "
                f"{config.SANDBOX_SCRATCH_ROOT} (admission floor "
                f"{config.SANDBOX_POOL_MIN_FREE_MB}MB); refusing sandbox for "
                f"{self.message_id}"
            )
            raise SandboxUnavailableError(
                "Sandbox storage is currently exhausted. "
                "Sandbox tools are unavailable for this turn."
            )

    async def ensure_container(self) -> None:
        """lazy 起容器(幂等)。失败 → SandboxUnavailableError,本 turn 不再重试。"""
        if self._closed:
            raise SandboxUnavailableError("Sandbox session is already closed for this turn.")
        if self._sticky_failure is not None:
            raise SandboxUnavailableError(self._sticky_failure)
        if self._container is not None:
            return

        try:
            async with asyncio.timeout(config.SANDBOX_START_TIMEOUT):
                os.makedirs(config.SANDBOX_SCRATCH_ROOT, exist_ok=True)
                self._check_pool_admission()
                self._prepare_scratch_dir()
                if self._docker is None:
                    self._docker = self._docker_factory()
                container = await self._docker.containers.create(
                    config=self._container_config(),
                    name=f"af-sandbox-{self.message_id}",
                )
                # 先记句柄再 start:start 失败/中途取消时 close() 仍能删到它
                self._container = container
                await container.start()
        except asyncio.CancelledError:
            # 取消不是失败:不写 _sticky_failure,半成品交给 turn 末 close()/reaper
            raise
        except SandboxUnavailableError as e:
            # 准入水位拒绝:消息已是模型面文案、日志已记,只补 sticky,不再 rewrap
            self._sticky_failure = str(e)
            raise
        except DockerError as e:
            if e.status == 404 and config.SANDBOX_IMAGE in str(e.message):
                msg = (
                    f"Sandbox image '{config.SANDBOX_IMAGE}' not found on the Docker daemon. "
                    "Sandbox tools are unavailable for this turn."
                )
                # operator 配置问题,无栈可用 → error
                logger.error(
                    f"Sandbox image missing: {config.SANDBOX_IMAGE} "
                    f"(conv={self.conversation_id}, msg={self.message_id})"
                )
            else:
                msg = (
                    f"Sandbox container failed to start (Docker error {e.status}). "
                    "Sandbox tools are unavailable for this turn."
                )
                logger.exception(
                    f"Sandbox container create/start failed for {self.message_id}: {e}"
                )
            self._sticky_failure = msg
            raise SandboxUnavailableError(msg) from e
        except TimeoutError as e:
            msg = (
                f"Sandbox container did not start within {config.SANDBOX_START_TIMEOUT}s. "
                "Sandbox tools are unavailable for this turn."
            )
            logger.error(
                f"Sandbox container start timed out for {self.message_id} "
                f"(daemon unresponsive?)"
            )
            self._sticky_failure = msg
            raise SandboxUnavailableError(msg) from e
        except Exception as e:
            msg = "Sandbox container failed to start. Sandbox tools are unavailable for this turn."
            logger.exception(f"Sandbox container create/start failed for {self.message_id}: {e}")
            self._sticky_failure = msg
            raise SandboxUnavailableError(msg) from e

        # 软配额 watchdog:容器活着的期间周期巡检本 turn scratch 的块占用,
        # 超额 → 杀容器 + sticky。close() 先 cancel 它再拆容器。
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        logger.info(
            f"Sandbox container started for {self.message_id} "
            f"(image={config.SANDBOX_IMAGE}, runtime={config.SANDBOX_RUNTIME or 'default'})"
        )

    async def _watchdog_loop(self) -> None:
        """per-turn 软配额巡检(C′ 第二层;第一层 loop 池子硬墙兜住其 race 窗口)。

        du(块占用)在 to_thread 跑;超 SANDBOX_WORKSPACE_QUOTA_MB → sticky +
        杀容器。探针②:杀容器时 in-flight exec 的 stream 正常 EOF、exit=137,
        exec() 末尾的 sticky 检查负责把它归因成配额失败而非裸 137。
        """
        quota_bytes = config.SANDBOX_WORKSPACE_QUOTA_MB * 1024 * 1024
        try:
            while True:
                await asyncio.sleep(config.SANDBOX_WATCHDOG_INTERVAL_SEC)
                try:
                    usage, incomplete = await asyncio.to_thread(
                        sandbox_fs.measure_usage, self._scratch_dir
                    )
                except Exception:
                    logger.exception(
                        f"Sandbox watchdog scan failed for {self.message_id}; retrying next tick"
                    )
                    continue
                # incomplete = 计量穷不尽(树太深 / 开不出 fd / chmod 000 藏子树 /
                # 被换链)→ fail-closed 当超额(绝不 fail-open 只计浅层:深埋大文件会
                # 绕软配额伤其他 turn,池子硬墙是最后而非唯一防线)。
                if incomplete:
                    await self._kill_over_quota(usage, measure_incomplete=True)
                    return
                if usage > quota_bytes:
                    await self._kill_over_quota(usage)
                    return
        except asyncio.CancelledError:
            raise

    async def _kill_over_quota(self, usage: int, *, measure_incomplete: bool = False) -> None:
        """超额处置:先置 sticky(in-flight exec 与后续调用都按它归因),再杀容器。"""
        self._sticky_failure = (
            f"Sandbox workspace exceeded the "
            f"{config.SANDBOX_WORKSPACE_QUOTA_MB}MB disk quota and was terminated. "
            "Sandbox tools are unavailable for this turn."
        )
        # 模型行为触发、预期内防护、已处置 → warning
        if measure_incomplete:
            logger.warning(
                f"Sandbox workspace usage could not be fully measured for {self.message_id} "
                f"({usage / 1024 / 1024:.0f}MB counted; tree too deep / fd-exhausted / "
                "unreadable subtree); treated as over quota (fail-closed), killing container"
            )
        else:
            logger.warning(
                f"Sandbox workspace over quota for {self.message_id}: "
                f"{usage / 1024 / 1024:.0f}MB used "
                f"(quota {config.SANDBOX_WORKSPACE_QUOTA_MB}MB); killing container"
            )
        container = self._container
        if container is None:
            return
        # 删**成功**才交出所有权(置 None):失败 / 弃等 / 被 close() cancel 打断时
        # 句柄必须留着,close() 会重删(404 容忍)—— 否则两边都不删 = 孤儿
        # (真机矩阵 case 6 实测踩中:close cancel 了 await 中的 delete)。
        try:
            # 有界弃等:daemon 卡死时不挂死 watchdog task(残留等 close()/reaper)
            async with asyncio.timeout(EXEC_ABANDON_GRACE_SEC):
                await container.delete(force=True)
            self._container = None
        except asyncio.CancelledError:
            raise
        except DockerError as e:
            if e.status == 404:
                self._container = None
            else:
                logger.error(
                    f"Over-quota sandbox container delete failed for {self.message_id} "
                    f"(status={e.status}); close()/reaper will collect it"
                )
        except TimeoutError:
            logger.error(
                f"Over-quota sandbox container delete timed out for {self.message_id}; "
                "close()/reaper will collect it"
            )
        except Exception:
            logger.exception(
                f"Over-quota sandbox container delete failed for {self.message_id}"
            )

    async def close(self) -> None:
        """拆容器 + 删 scratch + 关 client。幂等;每步独立 best-effort。

        由 execution_runner._wrapped 的真 finally 调用(成功/超时/协作取消/
        外部取消/崩溃五条退出都经过);任一步失败只记日志 —— reaper 兜底。
        """
        if self._closed:
            return
        self._closed = True

        # 先停 watchdog 再拆容器:避免它和 close 并发删同一容器 / 扫已删目录
        watchdog, self._watchdog_task = self._watchdog_task, None
        if watchdog is not None:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(f"Sandbox watchdog teardown failed for {self.message_id}")

        container, self._container = self._container, None
        if container is not None:
            try:
                await container.delete(force=True)
                logger.info(f"Sandbox container removed for {self.message_id}")
            except DockerError as e:
                if e.status != 404:
                    # 删失败 = 潜在孤儿容器,等 reaper;无栈价值 → error
                    logger.error(
                        f"Sandbox container delete failed for {self.message_id} "
                        f"(status={e.status}); reaper will collect it"
                    )
            except Exception:
                logger.exception(f"Sandbox container delete failed for {self.message_id}")

        if self._scratch_created:
            try:
                await asyncio.to_thread(shutil.rmtree, self._scratch_dir)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception(f"Sandbox scratch dir removal failed: {self._scratch_dir}")

        docker, self._docker = self._docker, None
        if docker is not None:
            try:
                await docker.close()
            except Exception:
                logger.exception(f"aiodocker client close failed for {self.message_id}")

    # ------------------------------------------------------------------
    # exec
    # ------------------------------------------------------------------

    async def exec(self, command: str) -> SandboxExecResult:
        """在容器内跑一条 bash 命令(lazy 起容器)。

        argv = ["timeout", "--signal=KILL", N, "bash", "-c", command]:
        command 整体是一个 argv 元素,无 shell 引号问题;到点 KILL 真杀。
        """
        await self.ensure_container()
        # 局部引用:watchdog 超额杀会把 self._container 置 None(与本协程并发)
        container = self._container
        if container is None:
            raise SandboxUnavailableError(
                self._sticky_failure
                or "Sandbox session is already closed for this turn."
            )

        argv = [
            "timeout",
            "--signal=KILL",
            str(config.SANDBOX_COMMAND_TIMEOUT),
            "bash",
            "-c",
            command,
        ]
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        try:
            async with asyncio.timeout(config.SANDBOX_COMMAND_TIMEOUT + EXEC_ABANDON_GRACE_SEC):
                exec_ = await container.exec(
                    argv, stdout=True, stderr=True, workdir=WORKSPACE_MOUNT
                )
                output, truncated = await self._drain_exec(exec_)
                exit_code = await self._resolve_exit_code(exec_)
        except TimeoutError as e:
            # 弃等不等于杀死:容器内进程可能还活着,turn 末拆容器收尾
            logger.error(
                f"Sandbox exec abandoned after "
                f"{config.SANDBOX_COMMAND_TIMEOUT + EXEC_ABANDON_GRACE_SEC}s "
                f"(msg={self.message_id}); container will be torn down at turn end"
            )
            raise SandboxExecTimeoutError(
                "Command did not return (exec channel unresponsive past the "
                f"{config.SANDBOX_COMMAND_TIMEOUT}s command timeout). "
                "The sandbox will be torn down at the end of this turn."
            ) from e
        except DockerError as e:
            # 容器中途消失(watchdog 超额杀 / 外力 rm):优先按 sticky 归因
            if self._sticky_failure is not None:
                raise SandboxUnavailableError(self._sticky_failure) from e
            logger.error(
                f"Sandbox container died during exec for {self.message_id} "
                f"(Docker error {e.status})"
            )
            self._sticky_failure = (
                "The sandbox container died while the command was running. "
                "Sandbox tools are unavailable for this turn."
            )
            raise SandboxUnavailableError(self._sticky_failure) from e

        # 探针②:watchdog 杀容器时 in-flight exec 多半正常返回 exit=137(stream
        # EOF、ExitCode 可解析)—— 裸 137 会被误读;sticky 已置时按配额失败归因。
        if self._sticky_failure is not None:
            raise SandboxUnavailableError(self._sticky_failure)

        return SandboxExecResult(
            exit_code=exit_code,
            output=output,
            truncated=truncated,
            duration=loop.time() - started_at,
        )

    async def _drain_exec(self, exec_) -> tuple:
        """读 multiplexed 流到 EOF,stdout/stderr 按到达序合流解码。

        超过 SANDBOX_MAX_OUTPUT_CHARS 后**继续 drain 但丢弃**(保持管道流动直到
        进程结束,同时不放大内存),截断打标。每流独立 incremental decoder,
        避免 frame 边界劈断多字节字符。
        """
        decoders = {
            1: codecs.getincrementaldecoder("utf-8")(errors="replace"),
            2: codecs.getincrementaldecoder("utf-8")(errors="replace"),
        }
        parts: list = []
        total = 0
        truncated = False
        async with exec_.start(detach=False) as stream:
            while True:
                message = await stream.read_out()
                if message is None:
                    break
                decoder = decoders.get(message.stream)
                if decoder is None:
                    continue
                text = decoder.decode(message.data)
                if not text:
                    continue
                if total >= config.SANDBOX_MAX_OUTPUT_CHARS:
                    truncated = True
                    continue
                room = config.SANDBOX_MAX_OUTPUT_CHARS - total
                if len(text) > room:
                    parts.append(text[:room])
                    total += room
                    truncated = True
                else:
                    parts.append(text)
                    total += len(text)
        return "".join(parts), truncated

    async def _resolve_exit_code(self, exec_) -> int:
        """EOF 后取 ExitCode;daemon 落账有延迟,有界轮询,取不到给 -1。"""
        for attempt in range(_EXIT_CODE_POLLS):
            info = await exec_.inspect()
            exit_code = info.get("ExitCode")
            if exit_code is not None and not info.get("Running", False):
                return exit_code
            await asyncio.sleep(_EXIT_CODE_POLL_INTERVAL)
        logger.warning(f"Sandbox exec exit code unresolved for {self.message_id}; reporting -1")
        return -1
