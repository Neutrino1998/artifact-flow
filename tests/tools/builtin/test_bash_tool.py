"""
BashTool unit tests — fake SandboxSession(契约层,不碰 docker)。
"""

import pytest

from config import config
from tools.base import ToolPermission
from tools.builtin.sandbox_ops import BashTool, create_sandbox_tools
from tools.builtin.sandbox_session import (
    SandboxExecResult,
    SandboxUnavailableError,
)


class FakeSession:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.commands: list[str] = []

    async def exec(self, command):
        self.commands.append(command)
        if self._error is not None:
            raise self._error
        return self._result


def _result(exit_code=0, output="hello\n", truncated=False, duration=0.5):
    return SandboxExecResult(
        exit_code=exit_code, output=output, truncated=truncated, duration=duration
    )


class TestBashTool:

    def test_identity(self):
        tool = BashTool(FakeSession())
        assert tool.name == "bash"
        assert tool.permission == ToolPermission.CONFIRM
        params = tool.get_parameters()
        assert [p.name for p in params] == ["command"]

    def test_factory(self):
        tools = create_sandbox_tools(FakeSession())
        assert [t.name for t in tools] == ["bash"]

    async def test_zero_exit_returns_plain_output(self):
        session = FakeSession(result=_result())
        result = await BashTool(session)(command="echo hello")
        assert result.success
        assert result.data == "hello"
        assert result.metadata["exit_code"] == 0
        assert session.commands == ["echo hello"]

    async def test_empty_output_placeholder(self):
        result = await BashTool(FakeSession(result=_result(output="")))(command="true")
        assert result.success
        assert result.data == "(no output)"

    async def test_nonzero_exit_is_information_not_failure(self):
        """grep 无命中 exit 1 是信息不是故障 —— success=True + 显式 exit code。"""
        result = await BashTool(FakeSession(result=_result(exit_code=2, output="oops\n")))(
            command="ls /nope"
        )
        assert result.success
        assert "oops" in result.data
        assert "[exit code: 2]" in result.data

    async def test_timeout_kill_attributed(self, monkeypatch):
        monkeypatch.setattr(config, "SANDBOX_COMMAND_TIMEOUT", 5)
        result = await BashTool(
            FakeSession(result=_result(exit_code=137, output="", duration=5.2))
        )(command="while true; do :; done")
        assert result.success
        assert "killed by the 5s command timeout" in result.data

    async def test_exit_137_below_timeout_not_misattributed(self, monkeypatch):
        """同码 137 也可能是 OOM-kill:时长没到超时就不能归因 timeout。"""
        monkeypatch.setattr(config, "SANDBOX_COMMAND_TIMEOUT", 100)
        result = await BashTool(
            FakeSession(result=_result(exit_code=137, output="", duration=2.0))
        )(command="python eat_memory.py")
        assert "[exit code: 137]" in result.data
        assert "timeout" not in result.data

    async def test_truncation_marker(self):
        result = await BashTool(FakeSession(result=_result(output="x" * 50, truncated=True)))(
            command="yes"
        )
        assert f"[output truncated at {config.SANDBOX_MAX_OUTPUT_CHARS} chars]" in result.data

    async def test_sandbox_unavailable_is_tool_failure(self):
        result = await BashTool(
            FakeSession(error=SandboxUnavailableError("Sandbox image 'x' not found"))
        )(command="echo hi")
        assert not result.success
        assert "not found" in result.error

    async def test_blank_command_rejected(self):
        result = await BashTool(FakeSession(result=_result()))(command="   ")
        assert not result.success

    async def test_missing_command_rejected(self):
        result = await BashTool(FakeSession(result=_result()))()
        assert not result.success
        assert "command" in result.error
