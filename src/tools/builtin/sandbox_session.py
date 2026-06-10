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
from dataclasses import dataclass
from typing import Callable, Optional

import aiodocker
from aiodocker.exceptions import DockerError

from config import config
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

# tool 侧 asyncio 弃等护栏 = 命令超时 + 此余量。正常路径由容器内 timeout 在
# SANDBOX_COMMAND_TIMEOUT 处收口;护栏只兜 daemon/exec 通道卡死。
EXEC_ABANDON_GRACE_SEC = 30

# ExitCode 在 stream EOF 后可能短暂为 None(daemon 异步落账),有界轮询。
_EXIT_CODE_POLLS = 20
_EXIT_CODE_POLL_INTERVAL = 0.05


def scratch_dir_name(conversation_id: str, message_id: str) -> str:
    """scratch 子目录名 —— reaper 的第二枚举源按此格式反解 per-turn 归属。

    conv-* / msg-* id 内部无双下划线,"__" 分隔无歧义。
    """
    return f"{conversation_id}__{message_id}"


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
        # 创建失败后本 turn 不重试(loud-fail 一次,后续调用立即复述原因):
        # 失败原因多为环境性(镜像缺失/daemon 不可达),turn 内重试只会重复烧启动超时。
        self._start_failure: Optional[str] = None
        self._scratch_dir = os.path.join(
            config.SANDBOX_SCRATCH_ROOT,
            scratch_dir_name(conversation_id, message_id),
        )
        self._scratch_created = False

    @property
    def started(self) -> bool:
        return self._container is not None

    @property
    def scratch_dir(self) -> str:
        return self._scratch_dir

    # ------------------------------------------------------------------
    # 容器生命周期
    # ------------------------------------------------------------------

    def _container_config(self) -> dict:
        mem_bytes = config.SANDBOX_MEM_LIMIT_MB * 1024 * 1024
        host_config = {
            "Binds": [f"{self._scratch_dir}:{WORKSPACE_MOUNT}:rw"],
            "NetworkMode": "none",                # 原则 7:默认全禁网
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
            "Labels": {
                SANDBOX_LABEL: "1",
                LABEL_NAMESPACE: config.REDIS_KEY_PREFIX or "default",
                LABEL_CONVERSATION: self.conversation_id,
                LABEL_MESSAGE: self.message_id,
            },
            "HostConfig": host_config,
        }

    def _prepare_scratch_dir(self) -> None:
        os.makedirs(self._scratch_dir, exist_ok=True)
        # 容器内 uid 1000(sandbox)要可写,backend 进程 uid 不定 → 0o777。
        # makedirs 的 mode 被 umask 掩掉,必须显式 chmod。真实 Linux 上的属主/
        # 权限策略是 D 阶段验收项(本机 Docker Desktop 感知不到 uid 错配)。
        os.chmod(self._scratch_dir, 0o777)
        self._scratch_created = True

    async def ensure_container(self) -> None:
        """lazy 起容器(幂等)。失败 → SandboxUnavailableError,本 turn 不再重试。"""
        if self._closed:
            raise SandboxUnavailableError("Sandbox session is already closed for this turn.")
        if self._start_failure is not None:
            raise SandboxUnavailableError(self._start_failure)
        if self._container is not None:
            return

        try:
            async with asyncio.timeout(config.SANDBOX_START_TIMEOUT):
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
            # 取消不是失败:不写 _start_failure,半成品交给 turn 末 close()/reaper
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
            self._start_failure = msg
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
            self._start_failure = msg
            raise SandboxUnavailableError(msg) from e
        except Exception as e:
            msg = "Sandbox container failed to start. Sandbox tools are unavailable for this turn."
            logger.exception(f"Sandbox container create/start failed for {self.message_id}: {e}")
            self._start_failure = msg
            raise SandboxUnavailableError(msg) from e

        logger.info(
            f"Sandbox container started for {self.message_id} "
            f"(image={config.SANDBOX_IMAGE}, runtime={config.SANDBOX_RUNTIME or 'default'})"
        )

    async def close(self) -> None:
        """拆容器 + 删 scratch + 关 client。幂等;每步独立 best-effort。

        由 execution_runner._wrapped 的真 finally 调用(成功/超时/协作取消/
        外部取消/崩溃五条退出都经过);任一步失败只记日志 —— reaper 兜底。
        """
        if self._closed:
            return
        self._closed = True

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
                exec_ = await self._container.exec(
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
