"""
SandboxSession — per-turn 沙盒容器生命周期

一个 turn 一个 session 对象壳:在 conversation_turn_factory 创建(同 ArtifactService,
构造注入沙盒工具),**容器 lazy 于首个沙盒工具调用** —— 多数 turn 不开沙盒,
eager 等于在多数 turn 上空转创建+销毁。拆除挂 TaskScope 的
真 finally（经 TaskScope 注册 LIFO cleanup），与 lease 同生灭。

所有 aiodocker 调用收口在本类这一个 seam 后(编排器可换性:将来 Docker↔k8s
只换该层,引擎无感)。容器创建参数(镜像/挂载/runtime/配额)全部来自代码侧
config,绝不可被模型生成内容污染 —— backend 持 docker.sock 等于 host root。

per-command 超时 = 容器内 `timeout --signal=KILL` 包 argv:exec API 收 argv
数组,cmd 整体是一个 argv 元素,无宿主侧 shell、无引号问题,且是**真杀进程**。
tool 侧的 asyncio 超时只是弃等(进程不死),残留进程由 turn 末
拆容器兜底。
"""

import asyncio
import codecs
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

import aiodocker
from aiodocker.exceptions import DockerError

from config import config
from tools.builtin import sandbox_fs
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# 容器内工作区挂载点。固定值非 operator 旋钮:工具描述/mount 返回值都引用它,
# 改路径要连动提示文案,不是配置能独立换的。
WORKSPACE_MOUNT = "/workspace"

# skill bundle 的 mount 根(mount_skill 把 bundle 解到 {WORKSPACE_MOUNT}/{SKILLS_SUBDIR}/<slug>/)。
# 保留名：artifact id 的格式允许字面 `.skills`，MountArtifactTool 必须拒绝该名称，
# 否则会与技能挂载目录冲突。
SKILLS_SUBDIR = ".skills"

# 容器/scratch 目录的归属标识。reaper 按 SANDBOX_LABEL 枚举 daemon 上
# 的活容器,再按 conv/msg label 与 list_active_executions 做 per-turn 差集;
# namespace label 隔离共用同一 daemon 的多套部署(各自的 reaper 只认本命名空间)。
SANDBOX_LABEL = "artifactflow.sandbox"
LABEL_NAMESPACE = f"{SANDBOX_LABEL}.namespace"
LABEL_CONVERSATION = f"{SANDBOX_LABEL}.conversation-id"
LABEL_MESSAGE = f"{SANDBOX_LABEL}.message-id"
LABEL_WORKER = f"{SANDBOX_LABEL}.worker-id"
LABEL_GENERATION = f"{SANDBOX_LABEL}.generation"

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
    """`scratch_dir_name` 的逆 → (conv, msg, worker) 或 None;worker 可能为 None。

    接受**两种**格式:当前三段 `{conv}__{msg}__{worker}`,与 worker-id 引入前的两段
    legacy `{conv}__{msg}`(worker=None)。conv/msg 内部无 "__" 保证前两段无歧义。
    段段非空、且恰好 2 或 3 段才算我们的目录;真正陌生的名字返回 None → reaper 跳过
    (不碰不是我们建的东西,免误删错配到共享根的别人目录)。

    为何认 legacy / 不收窄到 exact-3:final_sweep 只对 `worker==WORKER_ID` 绕 grace,
    worker=None 的目录照走普通周期 grace 即可回收。若 parse 只认三段,任何它不识别的
    名字(legacy、或将来再改格式)就被**静默永久跳过** —— 容器还能靠 label 收,纯目录
    残留却没有第二兜底来源 = silent leak(正是本项目反复在消灭的失败类)。
    """
    parts = name.split("__")
    if len(parts) not in (2, 3) or not all(parts):
        return None
    worker = parts[2] if len(parts) == 3 else None
    return parts[0], parts[1], worker


class SandboxError(Exception):
    """沙盒错误基类(工具层 catch 它转 loud-fail ToolResult)。

    ``diagnostics`` 是仅供 TOOL_COMPLETE.metadata / admin 审计的有界事实，
    不拼进模型面错误文案。ops 原始错误仍由日志记录。
    """

    def __init__(self, message: str, *, diagnostics: Optional[dict] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class SandboxUnavailableError(SandboxError):
    """启动/准入/sticky/恢复失败或 session 已关闭 —— 该 turn 沙盒不可用。"""


class SandboxCommandError(SandboxError):
    """当前命令失败，但 session 可能已换成一个可继续使用的空沙盒。"""


class SandboxStaleInvocationError(SandboxError):
    """调用生成于最近一次空现场恢复之前，未在新 generation 上执行。"""


class SandboxExecTimeoutError(SandboxCommandError):
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
    执行(见 core/execution/engine.py),故无并发起容器问题,不加锁。

    close() 幂等,且每步独立 best-effort(容器 → scratch → client):任一步失败
    记日志继续,残留由 lease-anchored reaper 兜底。
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
        # sticky 失败通道:创建失败 / 准入水位拒绝 / watchdog 超额杀 / 恢复失败,
        # 本 turn 不再重试(loud-fail 一次,后续调用立即复述原因)。已成功启动过的
        # 容器若 OOM/死亡/exec 通道失效，会先尝试一次「删旧现场 + 起全新空容器」；
        # 这不是状态恢复，旧 /workspace 与 /tmp 必须整体丢弃。
        self._sticky_failure: Optional[str] = None
        # 与 sticky 文案同生灭的 admin-only 结构化证据。后续工具重撞时
        # 也能复述原始根因，不会因容器已删而退化成通用错误。
        self._sticky_diagnostics: dict = {}
        self._scratch_dir = os.path.join(
            config.SANDBOX_SCRATCH_ROOT,
            scratch_dir_name(conversation_id, message_id),
        )
        self._scratch_created = False
        self._watchdog_task: Optional[asyncio.Task] = None
        # generation 1 是首次 lazy 容器；同 turn 至多 rollover 一次到 generation 2。
        # bool 闸比可调计数更贴合产品契约，也让无限重启循环不可表达。
        self._generation = 1
        self._recovery_attempted = False
        # generation rollover 会把最低有效 epoch 推到故障模型调用的下一次。
        # 同一 provider response 的 sibling calls 仍带旧 epoch，因而只能 loud-fail；
        # 跨 lead/subagent 的全 turn 单调序保证旧父调用返回后也无法误用新现场。
        self._minimum_valid_invocation_epoch = 1
        # 成功 rollover 后保留到 turn 结束，供每次动态状态注入持续纠正模型：
        # 当前目录是空的新现场，历史中的 mount / skill / 中间文件均已失效。
        self._last_recovery: Optional[dict] = None

    @property
    def started(self) -> bool:
        return self._container is not None

    @property
    def sticky_failure(self) -> Optional[str]:
        """本 turn 已记录的沙盒不可用原因(创建失败 / 准入拒绝 / 超额杀 /
        rollover 失败或次数用尽),None = 无。供不触发 ensure_container 的工具(persist)在
        其前置检查里复述配额失败,与 bash/mount 的 sticky 行为一致。"""
        return self._sticky_failure

    @property
    def sticky_diagnostics(self) -> dict:
        """Sticky 失败的有界 admin-only 证据；调用方不应修改。"""
        return self._sticky_diagnostics

    @property
    def generation(self) -> int:
        """当前沙盒代次；首次容器为 1，一次性空现场恢复成功后为 2。"""
        return self._generation

    @property
    def minimum_valid_invocation_epoch(self) -> int:
        """当前 generation 接受的最早 model invocation epoch。"""
        return self._minimum_valid_invocation_epoch

    def require_fresh_invocation(self, model_invocation_epoch: int) -> None:
        """拒绝基于 rollover 前模型上下文生成的沙盒调用。

        epoch 由引擎的 ``ToolExecutionContext`` 注入，turn 内跨 agent 单调递增；
        SandboxSession 只解释 freshness，不感知 tool batch / agent 递归。sticky
        优先，因为配额杀或 recovery 失败比 stale sibling 是更权威的不可用事实。
        """
        if self._sticky_failure is not None:
            raise self._sticky_error()
        if (
            not isinstance(model_invocation_epoch, int)
            or isinstance(model_invocation_epoch, bool)
            or model_invocation_epoch < 1
        ):
            logger.error(
                "Sandbox received invalid model invocation epoch %r for %s",
                model_invocation_epoch,
                self.message_id,
            )
            raise SandboxUnavailableError(
                "Sandbox invocation context is invalid; this sandbox call was not executed."
            )
        if model_invocation_epoch >= self._minimum_valid_invocation_epoch:
            return

        diagnostics = {
            "sandbox_invocation": {
                "stale": True,
                "invocation_epoch": model_invocation_epoch,
                "minimum_valid_epoch": self._minimum_valid_invocation_epoch,
                "generation": self._generation,
            }
        }
        logger.warning(
            "Skipped stale sandbox invocation for %s: epoch=%s, minimum=%s, generation=%s",
            self.message_id,
            model_invocation_epoch,
            self._minimum_valid_invocation_epoch,
            self._generation,
        )
        raise SandboxStaleInvocationError(
            "This sandbox call was generated before the sandbox was restarted and was "
            "not executed. Replan using the fresh empty sandbox and retry only the "
            "operations that are still needed.",
            diagnostics=diagnostics,
        )

    @staticmethod
    def _container_id(container: Any) -> str:
        """aiodocker 容器 id；假对象/创建早期取不到时显式 unknown。"""
        value = getattr(container, "_id", None)
        return str(value) if value else "unknown"

    def _set_sticky_failure(self, message: str, diagnostics: Optional[dict] = None) -> None:
        self._sticky_failure = message
        self._sticky_diagnostics = diagnostics or {}

    def _sticky_error(self) -> SandboxUnavailableError:
        return SandboxUnavailableError(
            self._sticky_failure or "Sandbox tools are unavailable for this turn.",
            diagnostics=self._sticky_diagnostics,
        )

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

    def status_snapshot(self) -> dict:
        """供 ContextManager 动态注入(<sandbox_status>)的轻量状态快照。

        历史里上一轮的 mount/bash 记录对模型是"文件还在"的伪证,静态描述里的
        per-turn ephemeral 规则压不过它 —— 注入"现在时态"的工作区事实才有效
        (同 artifact inventory 的存在理由)。三态:

        - not_started:本轮未起容器(注入文案传达"工作区为空、旧 mount 已失效")
        - unavailable:sticky 失败,复述原因(省掉模型再撞一次工具的回合)
        - running:工作区第一层清单(条数帽 SANDBOX_STATUS_MAX_ENTRIES,超出
          显式 truncated 标记),给 persist 的 path 决策当依据

        同步方法(单层枚举,调用方按需 to_thread);枚举走 sandbox_fs.list_dir
        (fd 钉住、不跟链、不递归)—— 工作区是模型可写的树,纪律同 reaper。
        **有界扫**(cap+1 即停):顶层条目数模型可控,全量物化是内存放大器;
        代价 = 展示的是 readdir 序前缀(组内再排序),且只知"有没有更多"、
        不知精确总数 —— glance 语义下足够。
        sticky 优先于 started 判定:超额杀后容器句柄已清但原因要复述。
        """
        if self._sticky_failure:
            snapshot = {
                "state": "unavailable",
                "reason": self._sticky_failure,
            }
            if self._generation > 1:
                snapshot["generation"] = self._generation
            return snapshot
        if not self.started:
            snapshot = {"state": "not_started"}
            if self._generation > 1:
                snapshot["generation"] = self._generation
            return snapshot
        cap = config.SANDBOX_STATUS_MAX_ENTRIES
        try:
            entries = sandbox_fs.list_dir(self.workspace_dir, max_entries=cap + 1)
        except OSError:
            logger.exception(f"workspace listing failed for {self.message_id}")
            snapshot = {
                "state": "running",
                "entries": None,
                "truncated": False,
            }
            if self._generation > 1:
                snapshot["generation"] = self._generation
            if self._last_recovery is not None:
                snapshot["recovery"] = dict(self._last_recovery)
            return snapshot
        truncated = len(entries) > cap
        shown = sorted(entries[:cap], key=lambda t: t[0])
        snapshot = {
            "state": "running",
            "entries": [(name, is_dir) for name, is_dir, _ in shown],
            "truncated": truncated,
        }
        if self._generation > 1:
            snapshot["generation"] = self._generation
        if self._last_recovery is not None:
            snapshot["recovery"] = dict(self._last_recovery)
        return snapshot

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
            "NetworkMode": "none",                # 默认全禁网
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
                LABEL_GENERATION: str(self._generation),
            },
            "HostConfig": host_config,
        }

    def _prepare_scratch_dir(self) -> None:
        # 容器内 uid 1000(sandbox)要可写,backend 进程 uid 不定 → 0o777。
        # makedirs 的 mode 被 umask 掩掉，必须显式 chmod。属主和权限策略须在真实
        # Linux 上验收，因为本机 Docker Desktop 感知不到 uid 错配。
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
            raise self._sticky_error()
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
                    name=(
                        f"af-sandbox-{self.message_id}"
                        if self._generation == 1
                        else f"af-sandbox-{self.message_id}-g{self._generation}"
                    ),
                )
                # 先记句柄再 start:start 失败/中途取消时 close() 仍能删到它
                self._container = container
                await container.start()
        except asyncio.CancelledError:
            # 取消不是失败:不写 _sticky_failure,半成品交给 turn 末 close()/reaper
            raise
        except SandboxUnavailableError as e:
            # 准入水位拒绝:消息已是模型面文案、日志已记,只补 sticky,不再 rewrap
            self._set_sticky_failure(str(e), e.diagnostics)
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
            self._set_sticky_failure(msg)
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
            self._set_sticky_failure(msg)
            raise SandboxUnavailableError(msg) from e
        except Exception as e:
            msg = "Sandbox container failed to start. Sandbox tools are unavailable for this turn."
            logger.exception(f"Sandbox container create/start failed for {self.message_id}: {e}")
            self._set_sticky_failure(msg)
            raise SandboxUnavailableError(msg) from e

        # 软配额 watchdog:容器活着的期间周期巡检本 turn scratch 的块占用,
        # 超额 → 杀容器 + sticky。close() 先 cancel 它再拆容器。
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        logger.info(
            f"Sandbox container started for {self.message_id} "
            f"(generation={self._generation}, "
            f"container_id={self._container_id(self._container)}, "
            f"image={config.SANDBOX_IMAGE}, runtime={config.SANDBOX_RUNTIME or 'default'})"
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
        message = (
            f"Sandbox workspace exceeded the "
            f"{config.SANDBOX_WORKSPACE_QUOTA_MB}MB disk quota and was terminated. "
            "Sandbox tools are unavailable for this turn."
        )
        self._set_sticky_failure(
            message,
            {
                "sandbox_failure": {
                    "failure_kind": "workspace_quota",
                    "container_id": self._container_id(self._container),
                    "observed_workspace_mb": round(usage / 1024 / 1024, 2),
                    "limits": {
                        "workspace_mb": config.SANDBOX_WORKSPACE_QUOTA_MB,
                    },
                }
            },
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

    async def _stop_watchdog(self) -> None:
        """停止并清空当前 generation 的 watchdog；close / rollover 共用。"""
        watchdog, self._watchdog_task = self._watchdog_task, None
        if watchdog is None:
            return
        # 当前仅 close / exec 故障恢复会调用；防未来从 watchdog 自身调用时自等死。
        if watchdog is asyncio.current_task():
            return
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(f"Sandbox watchdog teardown failed for {self.message_id}")

    @staticmethod
    def _diagnostics_with_recovery(
        diagnostics: dict,
        *,
        succeeded: bool,
        generation: int,
        workspace_reset: bool,
        failure_stage: Optional[str] = None,
    ) -> dict:
        merged = dict(diagnostics)
        recovery = {
            "attempted": True,
            "succeeded": succeeded,
            "generation": generation,
            "workspace_reset": workspace_reset,
        }
        if failure_stage is not None:
            recovery["failure_stage"] = failure_stage
        merged["sandbox_recovery"] = recovery
        return merged

    def _permanent_failure_after_recovery(
        self,
        cause: str,
        diagnostics: dict,
        *,
        generation: int,
        workspace_reset: bool,
        failure_stage: str,
    ) -> SandboxUnavailableError:
        merged = self._diagnostics_with_recovery(
            diagnostics,
            succeeded=False,
            generation=generation,
            workspace_reset=workspace_reset,
            failure_stage=failure_stage,
        )
        message = (
            f"{cause} Automatic sandbox recovery failed; sandbox tools are unavailable "
            "for the rest of this turn."
        )
        self._set_sticky_failure(message, merged)
        return self._sticky_error()

    async def _recover_after_runtime_failure(
        self,
        *,
        container: Any,
        failure_kind: str,
        diagnostics: dict,
        model_invocation_epoch: int,
    ) -> SandboxError:
        """把已启动后损坏的容器 rollover 成一个全新空 generation。

        当前命令永不重放：即使 Docker 没返回结果，它也可能已经产生过部分副作用。
        新容器只有在旧容器删除和整个 scratch 删除都得到确认后才启动，故不会出现
        两个 generation 共用同一 bind 目录。每 turn 只允许一次尝试；第二次故障、
        任一清理/启动失败均转 sticky。workspace quota 不会走到本函数。
        """
        cause = self._failure_message(failure_kind)

        # watchdog 可能恰在 exec 归因期间先写入更精确的 quota sticky；先停再复查，
        # quota 事实优先且不消耗一次 runtime recovery。
        await self._stop_watchdog()
        if self._sticky_failure is not None:
            return self._sticky_error()

        if self._recovery_attempted:
            logger.warning(
                f"Sandbox recovery not retried for generation {self._generation} "
                f"of {self.message_id} after {failure_kind}: one-turn allowance already used"
            )
            message = (
                f"{cause} The sandbox has already been restarted once this turn, so it "
                "will not be restarted again. Sandbox tools are unavailable for the rest "
                "of this turn."
            )
            self._set_sticky_failure(message, diagnostics)
            return self._sticky_error()

        self._recovery_attempted = True
        target_generation = self._generation + 1

        # 删除成功或 404 才能交出旧句柄所有权；超时/不明错误保留句柄给 close/reaper，
        # 且绝不删除 scratch / 起新容器。
        try:
            async with asyncio.timeout(EXEC_ABANDON_GRACE_SEC):
                await container.delete(force=True)
        except asyncio.CancelledError:
            raise
        except DockerError as e:
            if e.status != 404:
                logger.error(
                    f"Sandbox recovery could not delete generation {self._generation} "
                    f"for {self.message_id} (status={e.status}); refusing replacement"
                )
                return self._permanent_failure_after_recovery(
                    cause,
                    diagnostics,
                    generation=target_generation,
                    workspace_reset=False,
                    failure_stage="container_delete",
                )
        except TimeoutError:
            logger.error(
                f"Sandbox recovery timed out deleting generation {self._generation} "
                f"for {self.message_id}; refusing replacement"
            )
            return self._permanent_failure_after_recovery(
                cause,
                diagnostics,
                generation=target_generation,
                workspace_reset=False,
                failure_stage="container_delete",
            )
        except Exception:
            logger.exception(
                f"Sandbox recovery failed deleting generation {self._generation} "
                f"for {self.message_id}; refusing replacement"
            )
            return self._permanent_failure_after_recovery(
                cause,
                diagnostics,
                generation=target_generation,
                workspace_reset=False,
                failure_stage="container_delete",
            )

        if self._container is container:
            self._container = None

        try:
            if self._scratch_created:
                await asyncio.to_thread(shutil.rmtree, self._scratch_dir)
            self._scratch_created = False
        except FileNotFoundError:
            self._scratch_created = False
        except Exception:
            logger.exception(
                f"Sandbox recovery failed clearing scratch for generation "
                f"{self._generation} ({self.message_id}); refusing replacement"
            )
            return self._permanent_failure_after_recovery(
                cause,
                diagnostics,
                generation=target_generation,
                workspace_reset=False,
                failure_stage="workspace_cleanup",
            )

        self._generation = target_generation
        try:
            await self.ensure_container()
        except asyncio.CancelledError:
            raise
        except SandboxError:
            # ensure_container 已按启动故障类型写 ops 日志；这里把模型面契约收回到
            # 原命令失败 + recovery 失败，并保留原始 runtime 诊断为主证据。
            return self._permanent_failure_after_recovery(
                cause,
                diagnostics,
                generation=target_generation,
                workspace_reset=True,
                failure_stage="container_start",
            )

        recovery = {
            "attempted": True,
            "succeeded": True,
            "generation": target_generation,
            "workspace_reset": True,
            "failure_kind": failure_kind,
        }
        self._last_recovery = recovery
        self._minimum_valid_invocation_epoch = model_invocation_epoch + 1
        merged = self._diagnostics_with_recovery(
            diagnostics,
            succeeded=True,
            generation=target_generation,
            workspace_reset=True,
        )
        message = (
            f"{cause} The failed command was not retried. A fresh empty sandbox has "
            "started successfully. Everything previously under /workspace, including "
            "mounted artifacts, mounted skills, and unpersisted files, was discarded. "
            "Re-mount or recreate required files before continuing."
        )
        logger.info(
            f"Sandbox recovered with empty generation {target_generation} for "
            f"{self.message_id} after {failure_kind}"
        )
        if failure_kind == "exec_channel_timeout":
            return SandboxExecTimeoutError(message, diagnostics=merged)
        return SandboxCommandError(message, diagnostics=merged)

    async def close(self) -> None:
        """拆容器 + 删 scratch + 关 client。幂等;每步独立 best-effort。

        由 TaskScope 的最外层 finally 调用(成功/超时/协作取消/
        外部取消/崩溃五条退出都经过);任一步失败只记日志 —— reaper 兜底。
        """
        if self._closed:
            return
        self._closed = True

        # 先停 watchdog 再拆容器:避免它和 close 并发删同一容器 / 扫已删目录
        await self._stop_watchdog()

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

    async def _inspect_container_best_effort(
        self, container: Any, *, phase: str
    ) -> Optional[dict]:
        """Return a bounded container-state snapshot without making observation authoritative.

        One local Docker inspect after each exec closes the reachable gap where runsc returns
        an exec result (for example WaitPID EOF) even though the per-turn container has stopped.
        Inspect timeout/failure is observer failure: log it and preserve the command result.
        A successful snapshot with ``running is False`` is authoritative and handled by exec().
        """
        container_id = self._container_id(container)
        try:
            async with asyncio.timeout(config.SANDBOX_INSPECT_TIMEOUT_SEC):
                info = await container.show()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                f"Sandbox container inspect timed out after "
                f"{config.SANDBOX_INSPECT_TIMEOUT_SEC}s during {phase} "
                f"(container_id={container_id}, msg={self.message_id}); "
                "preserving the command result"
            )
            return None
        except DockerError as e:
            # 404 是目标容器已不存在的权威事实，不是观测器本身失败。
            # 不猜 OOM，但当前命令不能继续被标成成功。
            if e.status == 404:
                return {
                    "container_id": container_id,
                    "inspect_available": False,
                    "state": {
                        "running": False,
                        "dead": None,
                        "oom_killed": None,
                        "exit_code": None,
                        "error": "container_not_found",
                        "finished_at": None,
                    },
                }
            logger.warning(
                f"Sandbox container inspect failed during {phase} "
                f"(container_id={container_id}, msg={self.message_id}, "
                f"status={e.status}, message={e.message!r}); preserving the command result"
            )
            return None
        except Exception:
            logger.exception(
                f"Sandbox container inspect failed during {phase} "
                f"(container_id={container_id}, msg={self.message_id}); "
                "preserving the command result"
            )
            return None

        if not isinstance(info, dict) or not isinstance(info.get("State"), dict):
            logger.warning(
                f"Sandbox container inspect returned malformed state during {phase} "
                f"(container_id={container_id}, msg={self.message_id}); "
                "preserving the command result"
            )
            return None

        state = info["State"]
        return {
            "container_id": str(info.get("Id") or container_id),
            "inspect_available": True,
            "state": {
                "running": state.get("Running"),
                "dead": state.get("Dead"),
                "oom_killed": state.get("OOMKilled"),
                "exit_code": state.get("ExitCode"),
                "error": state.get("Error") or "",
                "finished_at": state.get("FinishedAt"),
            },
        }

    def _build_failure_diagnostics(
        self,
        container: Any,
        *,
        failure_kind: str,
        snapshot: Optional[dict],
        exec_exit_code: Optional[int] = None,
        exec_duration: Optional[float] = None,
        output_truncated: Optional[bool] = None,
        docker_error_status: Optional[int] = None,
    ) -> dict:
        item = {
            "failure_kind": failure_kind,
            "container_id": (
                snapshot.get("container_id") if snapshot else self._container_id(container)
            ),
            "runtime": config.SANDBOX_RUNTIME or "default",
            "inspect_available": (
                snapshot.get("inspect_available", True) if snapshot else False
            ),
            "state": snapshot.get("state") if snapshot else None,
            "limits": {
                "memory_mb": config.SANDBOX_MEM_LIMIT_MB,
                "cpu": config.SANDBOX_CPU_LIMIT,
                "pids": config.SANDBOX_PIDS_LIMIT,
            },
        }
        if exec_exit_code is not None or exec_duration is not None:
            item["exec"] = {
                "exit_code": exec_exit_code,
                "duration_sec": round(exec_duration, 2) if exec_duration is not None else None,
                "output_truncated": output_truncated,
            }
        if docker_error_status is not None:
            item["docker_error_status"] = docker_error_status
        return {"sandbox_failure": item}

    @staticmethod
    def _stopped_failure_kind(snapshot: dict) -> str:
        state = snapshot.get("state") or {}
        return "oom" if state.get("oom_killed") is True else "container_stopped"

    def _failure_message(self, failure_kind: str) -> str:
        if failure_kind == "oom":
            return (
                f"The sandbox container exceeded its {config.SANDBOX_MEM_LIMIT_MB}MB "
                "memory limit and was terminated."
            )
        if failure_kind == "container_stopped":
            return "The sandbox container died while the command was running."
        if failure_kind == "exec_channel_timeout":
            return (
                "The sandbox command channel remained unresponsive past the command timeout."
            )
        return "The sandbox command channel failed."

    # ------------------------------------------------------------------
    # exec
    # ------------------------------------------------------------------

    async def exec(
        self,
        command: str,
        *,
        model_invocation_epoch: int,
    ) -> SandboxExecResult:
        """在容器内跑一条 bash 命令(lazy 起容器)。

        argv = ["timeout", "--signal=KILL", N, "bash", "-c", command]:
        command 整体是一个 argv 元素,无 shell 引号问题;到点 KILL 真杀。

        ``model_invocation_epoch`` 必须由调用者显式提供；生产沙盒工具取自
        ToolExecutionContext，低层手工探针/单测也必须声明它模拟的调用边界。
        """
        self.require_fresh_invocation(model_invocation_epoch)
        await self.ensure_container()
        # 局部引用:watchdog 超额杀会把 self._container 置 None(与本协程并发)
        container = self._container
        if container is None:
            if self._sticky_failure is not None:
                raise self._sticky_error()
            raise SandboxUnavailableError("Sandbox session is already closed for this turn.")

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
            # 弃等不等于杀死:容器内进程可能还活着。原命令绝不重放；只有确认
            # force-delete 旧容器 + 清空 scratch 后才起一个新 generation。
            duration = loop.time() - started_at
            diagnostics = self._build_failure_diagnostics(
                container,
                failure_kind="exec_channel_timeout",
                snapshot=None,
                exec_duration=duration,
            )
            logger.error(
                f"Sandbox exec abandoned after "
                f"{config.SANDBOX_COMMAND_TIMEOUT + EXEC_ABANDON_GRACE_SEC}s "
                f"(container_id={self._container_id(container)}, msg={self.message_id}); "
                "attempting one-time empty sandbox recovery"
            )
            error = await self._recover_after_runtime_failure(
                container=container,
                failure_kind="exec_channel_timeout",
                diagnostics=diagnostics,
                model_invocation_epoch=model_invocation_epoch,
            )
            raise error from e
        except DockerError as e:
            # 容器中途消失(watchdog 超额杀 / 外力 rm):优先按 sticky 归因
            if self._sticky_failure is not None:
                raise self._sticky_error() from e
            snapshot = await self._inspect_container_best_effort(
                container, phase="exec Docker error"
            )
            # inspect await 期间 quota watchdog 可能已经冻结了更精确的失败归因；
            # sticky 必须胜过随后观察到的通用 stopped/404，不能被覆盖。
            if self._sticky_failure is not None:
                raise self._sticky_error() from e
            state = snapshot.get("state") if snapshot else None
            if state is not None and state.get("running") is False:
                failure_kind = self._stopped_failure_kind(snapshot)
            elif snapshot is not None:
                failure_kind = "exec_channel_error"
            else:
                failure_kind = "docker_error"
            diagnostics = self._build_failure_diagnostics(
                container,
                failure_kind=failure_kind,
                snapshot=snapshot,
                exec_duration=loop.time() - started_at,
                docker_error_status=e.status,
            )
            log = logger.warning if failure_kind == "oom" else logger.error
            log(
                "Sandbox exec Docker error for %s "
                "(container_id=%s, status=%s, message=%r, diagnostics=%s)",
                self.message_id,
                self._container_id(container),
                e.status,
                e.message,
                diagnostics["sandbox_failure"],
            )
            error = await self._recover_after_runtime_failure(
                container=container,
                failure_kind=failure_kind,
                diagnostics=diagnostics,
                model_invocation_epoch=model_invocation_epoch,
            )
            raise error from e

        # 探针②:watchdog 杀容器时 in-flight exec 多半正常返回 exit=137(stream
        # EOF、ExitCode 可解析)—— 裸 137 会被误读;sticky 已置时按配额失败归因。
        if self._sticky_failure is not None:
            raise self._sticky_error()

        duration = loop.time() - started_at
        snapshot = await self._inspect_container_best_effort(container, phase="post-exec")
        # inspect await 期 watchdog 可能刚置 sticky 并删容器；二次检查关掉
        # 「配额杀发生在首次检查之后」的窄竞态窗口。
        if self._sticky_failure is not None:
            raise self._sticky_error()
        state = snapshot.get("state") if snapshot else None
        if state is not None and state.get("running") is False:
            failure_kind = self._stopped_failure_kind(snapshot)
            diagnostics = self._build_failure_diagnostics(
                container,
                failure_kind=failure_kind,
                snapshot=snapshot,
                exec_exit_code=exit_code,
                exec_duration=duration,
                output_truncated=truncated,
            )
            log = logger.warning if failure_kind == "oom" else logger.error
            log(
                "Sandbox container stopped before exec result return for %s "
                "(container_id=%s, diagnostics=%s)",
                self.message_id,
                self._container_id(container),
                diagnostics["sandbox_failure"],
            )
            error = await self._recover_after_runtime_failure(
                container=container,
                failure_kind=failure_kind,
                diagnostics=diagnostics,
                model_invocation_epoch=model_invocation_epoch,
            )
            raise error

        return SandboxExecResult(
            exit_code=exit_code,
            output=output,
            truncated=truncated,
            duration=duration,
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
