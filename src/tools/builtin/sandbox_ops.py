"""
沙盒工具(模型面)— C 阶段

三个分立动词(bash / mount / persist)共享一个 per-turn SandboxSession
(拍定 2026-06-03:分立参数面更小、对小模型更可读;共享 session 是实现层事实)。
本切片(C-session)先落 bash;mount / persist 在 C-stage。

工厂 create_sandbox_tools 由 controller_factory 按请求调用(同
create_artifact_tools idiom),session 构造注入。
"""

from typing import List

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.builtin.sandbox_session import (
    SandboxError,
    SandboxSession,
    WORKSPACE_MOUNT,
)
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class BashTool(BaseTool):
    """在 per-turn 沙盒容器内执行 bash 命令。

    - CONFIRM 权限:跑不可信(模型生成)代码。
    - 唯一参数 command —— 超时/配额/输出帽全是隐藏常量(参数面最小化)。
    - 命令退出码非零不算工具失败(grep 无命中 exit 1 是信息不是故障):
      success=True + 输出里带 exit code,让模型自己解读。success=False 只留给
      基建故障(容器起不来 / exec 通道卡死)。
    - 输出溢出分两层:session 侧 SANDBOX_MAX_OUTPUT_CHARS 硬帽(防内存放大,
      显式截断标记);>max_result_size_chars(50k)的部分由引擎溢出转 artifact
      idiom 接手,引擎零改动。
    """

    def __init__(self, session: SandboxSession):
        # TODO(C-wire): 描述补环境能力清单(python 科学栈/pandoc/ripgrep/git 版本)
        # —— 一次性事实进描述,场景 how-to 留 skill 系统。
        super().__init__(
            name="bash",
            description=(
                "Run a bash command inside this conversation's sandboxed Linux container. "
                "The sandbox has NO network access. Preinstalled: Python 3.11 with a "
                "scientific stack (numpy/pandas/matplotlib/openpyxl), pandoc, ripgrep. "
                f"The working directory {WORKSPACE_MOUNT} persists across bash calls "
                "within the current turn and is discarded when the turn ends. "
                f"Each command is killed after {config.SANDBOX_COMMAND_TIMEOUT}s."
            ),
            permission=ToolPermission.CONFIRM,
        )
        self._session = session

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="command",
                type="string",
                description="The bash command to run (executed via `bash -c`).",
                required=True,
            ),
        ]

    async def execute(self, command: str) -> ToolResult:
        if not command.strip():
            return ToolResult(success=False, error="Parameter 'command' must not be empty.")

        try:
            result = await self._session.exec(command)
        except SandboxError as e:
            # session 侧已按错误类型记过 ops 日志,这里只回模型面文案
            return ToolResult(success=False, error=str(e))

        lines = [result.output.rstrip("\n") if result.output.strip() else "(no output)"]
        if result.truncated:
            lines.append(
                f"[output truncated at {config.SANDBOX_MAX_OUTPUT_CHARS} chars]"
            )
        if result.exit_code != 0:
            note = f"[exit code: {result.exit_code}]"
            # timeout --signal=KILL 强杀 → 128+9;同码也可能是 OOM-kill,按时长归因
            if result.exit_code == 137 and result.duration >= config.SANDBOX_COMMAND_TIMEOUT:
                note = (
                    f"[exit code: 137 — killed by the "
                    f"{config.SANDBOX_COMMAND_TIMEOUT}s command timeout]"
                )
            lines.append(note)

        return ToolResult(
            success=True,
            data="\n".join(lines),
            metadata={
                "exit_code": result.exit_code,
                "duration_sec": round(result.duration, 2),
                "truncated": result.truncated,
            },
        )


def create_sandbox_tools(session: SandboxSession) -> List[BaseTool]:
    """创建沙盒工具(工厂,按请求调用;C-stage 在此追加 mount/persist)。"""
    return [
        BashTool(session),
    ]
