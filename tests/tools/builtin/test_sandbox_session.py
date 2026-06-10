"""
SandboxSession unit tests — fake aiodocker (no daemon needed).

真 docker 路径(multiplexed demux / 无孤儿矩阵)走 tests/manual/
sandbox_no_orphan_matrix.py 自验。
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from aiodocker.exceptions import DockerError

from config import config
from tools.builtin.sandbox_session import (
    SandboxExecTimeoutError,
    SandboxSession,
    SandboxUnavailableError,
    WORKSPACE_MOUNT,
    scratch_dir_name,
)


# ============================================================
# Fake aiodocker
# ============================================================


class FakeStream:
    def __init__(self, messages):
        self._messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read_out(self):
        if self._messages:
            return self._messages.pop(0)
        return None


class FakeExec:
    def __init__(self, messages, inspect_results):
        self._messages = messages
        # inspect() 逐次弹出,最后一个值粘住(模拟 daemon 落账延迟)
        self._inspect_results = list(inspect_results)
        self.inspect_calls = 0

    def start(self, detach=False):
        return FakeStream(self._messages)

    async def inspect(self):
        self.inspect_calls += 1
        if len(self._inspect_results) > 1:
            return self._inspect_results.pop(0)
        return self._inspect_results[0]


class FakeContainer:
    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.started = False
        self.deleted_with = None
        self.exec_calls: list[dict] = []
        self.next_exec = FakeExec(
            messages=[], inspect_results=[{"ExitCode": 0, "Running": False}]
        )

    async def start(self):
        if self.fail_start:
            raise DockerError(500, {"message": "start failed"})
        self.started = True

    async def delete(self, force=False):
        self.deleted_with = {"force": force}

    async def exec(self, cmd, stdout=True, stderr=True, workdir=None):
        self.exec_calls.append({"cmd": cmd, "workdir": workdir})
        return self.next_exec


class FakeContainers:
    def __init__(self, docker):
        self._docker = docker

    async def create(self, config, name=None):
        self._docker.create_calls.append({"config": config, "name": name})
        if self._docker.create_error is not None:
            raise self._docker.create_error
        container = FakeContainer(fail_start=self._docker.fail_start)
        self._docker.created_containers.append(container)
        return container


class FakeDocker:
    def __init__(self):
        self.containers = FakeContainers(self)
        self.create_calls: list[dict] = []
        self.created_containers: list[FakeContainer] = []
        self.create_error = None
        self.fail_start = False
        self.closed = False

    async def close(self):
        self.closed = True


def _msg(data: bytes, stream: int = 1):
    return SimpleNamespace(stream=stream, data=data)


@pytest.fixture
def scratch_root(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    monkeypatch.setattr(config, "SANDBOX_SCRATCH_ROOT", str(root))
    return root


@pytest.fixture
def fake_docker():
    return FakeDocker()


@pytest.fixture
def session(scratch_root, fake_docker):
    return SandboxSession("conv-abc", "msg-def", docker_factory=lambda: fake_docker)


# ============================================================
# 生命周期
# ============================================================


class TestLifecycle:

    async def test_shell_is_lazy(self, session, fake_docker):
        """壳创建零成本:不碰 docker、不建目录。"""
        assert not session.started
        assert fake_docker.create_calls == []
        assert not os.path.exists(session.scratch_dir)

    async def test_first_exec_starts_container_once(self, session, fake_docker):
        await session.exec("echo hi")
        await session.exec("echo again")
        assert len(fake_docker.create_calls) == 1
        assert fake_docker.created_containers[0].started

    async def test_scratch_dir_created_world_writable(self, session):
        await session.ensure_container()
        assert os.path.isdir(session.scratch_dir)
        assert os.path.basename(session.scratch_dir) == scratch_dir_name("conv-abc", "msg-def")
        # 容器内 uid 1000 须可写(D 阶段在真实 Linux 上复验属主策略)
        assert os.stat(session.scratch_dir).st_mode & 0o777 == 0o777

    async def test_container_config_is_code_sided(self, session, fake_docker, monkeypatch):
        monkeypatch.setattr(config, "SANDBOX_RUNTIME", "runsc")
        await session.ensure_container()
        call = fake_docker.create_calls[0]
        cfg = call["config"]
        host = cfg["HostConfig"]

        assert cfg["Image"] == config.SANDBOX_IMAGE
        assert cfg["Cmd"] == ["sleep", "infinity"]
        assert cfg["WorkingDir"] == WORKSPACE_MOUNT
        assert host["NetworkMode"] == "none"
        assert host["Binds"] == [f"{session.scratch_dir}:{WORKSPACE_MOUNT}:rw"]
        assert host["Runtime"] == "runsc"
        assert host["AutoRemove"] is False
        assert host["Memory"] == host["MemorySwap"]  # 禁 swap
        assert host["PidsLimit"] == config.SANDBOX_PIDS_LIMIT
        # reaper 对账标识:label 到 turn 粒度 + 容器名带 message_id
        labels = cfg["Labels"]
        assert labels["artifactflow.sandbox"] == "1"
        assert labels["artifactflow.sandbox.conversation-id"] == "conv-abc"
        assert labels["artifactflow.sandbox.message-id"] == "msg-def"
        assert call["name"] == "af-sandbox-msg-def"

    async def test_default_runtime_omitted(self, session, fake_docker, monkeypatch):
        monkeypatch.setattr(config, "SANDBOX_RUNTIME", "")
        await session.ensure_container()
        assert "Runtime" not in fake_docker.create_calls[0]["config"]["HostConfig"]

    async def test_create_failure_is_loud_and_sticky(self, scratch_root, fake_docker):
        fake_docker.create_error = DockerError(500, {"message": "daemon down"})
        session = SandboxSession("conv-a", "msg-b", docker_factory=lambda: fake_docker)

        with pytest.raises(SandboxUnavailableError):
            await session.exec("echo hi")
        # 本 turn 不重试:第二次立即复述失败,不再打 daemon
        with pytest.raises(SandboxUnavailableError):
            await session.exec("echo hi")
        assert len(fake_docker.create_calls) == 1

    async def test_missing_image_message_names_the_image(self, scratch_root, fake_docker):
        fake_docker.create_error = DockerError(
            404, {"message": f"No such image: {config.SANDBOX_IMAGE}"}
        )
        session = SandboxSession("conv-a", "msg-b", docker_factory=lambda: fake_docker)
        with pytest.raises(SandboxUnavailableError, match=config.SANDBOX_IMAGE):
            await session.exec("echo hi")

    async def test_start_failure_keeps_handle_for_close(self, scratch_root, fake_docker):
        """create 成功 start 失败:句柄已记,close() 仍能删到半成品容器。"""
        fake_docker.fail_start = True
        session = SandboxSession("conv-a", "msg-b", docker_factory=lambda: fake_docker)
        with pytest.raises(SandboxUnavailableError):
            await session.exec("echo hi")

        await session.close()
        assert fake_docker.created_containers[0].deleted_with == {"force": True}


class TestClose:

    async def test_close_tears_down_everything(self, session, fake_docker):
        await session.exec("echo hi")
        assert os.path.isdir(session.scratch_dir)

        await session.close()
        assert fake_docker.created_containers[0].deleted_with == {"force": True}
        assert not os.path.exists(session.scratch_dir)
        assert fake_docker.closed

    async def test_close_is_idempotent(self, session, fake_docker):
        await session.exec("echo hi")
        await session.close()
        fake_docker.created_containers[0].deleted_with = None
        await session.close()
        assert fake_docker.created_containers[0].deleted_with is None

    async def test_close_without_start_touches_nothing(self, session, fake_docker):
        await session.close()
        assert fake_docker.create_calls == []
        assert not fake_docker.closed  # client 从未创建

    async def test_close_survives_delete_failure(self, session, fake_docker, scratch_root):
        """容器删失败(reaper 兜)不阻断 scratch/client 清理。"""
        await session.exec("echo hi")
        container = fake_docker.created_containers[0]

        async def boom(force=False):
            raise DockerError(500, {"message": "daemon hiccup"})

        container.delete = boom
        await session.close()
        assert not os.path.exists(session.scratch_dir)
        assert fake_docker.closed

    async def test_exec_after_close_raises(self, session):
        await session.close()
        with pytest.raises(SandboxUnavailableError):
            await session.exec("echo hi")


# ============================================================
# exec
# ============================================================


class TestExec:

    async def test_command_wrapped_in_container_timeout_argv(self, session, fake_docker, monkeypatch):
        monkeypatch.setattr(config, "SANDBOX_COMMAND_TIMEOUT", 42)
        cmd = "echo \"it's got 'quotes' $and $(subshells)\""
        await session.exec(cmd)
        call = fake_docker.created_containers[0].exec_calls[0]
        # cmd 整体一个 argv 元素 —— 无宿主侧 shell、无引号问题
        assert call["cmd"] == ["timeout", "--signal=KILL", "42", "bash", "-c", cmd]
        assert call["workdir"] == WORKSPACE_MOUNT

    async def test_output_merged_in_arrival_order(self, session, fake_docker):
        await session.ensure_container()
        container = fake_docker.created_containers[0]
        container.next_exec = FakeExec(
            messages=[_msg(b"out1\n", 1), _msg(b"err1\n", 2), _msg(b"out2\n", 1)],
            inspect_results=[{"ExitCode": 3, "Running": False}],
        )
        result = await session.exec("whatever")
        assert result.output == "out1\nerr1\nout2\n"
        assert result.exit_code == 3
        assert not result.truncated

    async def test_multibyte_split_across_frames(self, session, fake_docker):
        await session.ensure_container()
        container = fake_docker.created_containers[0]
        encoded = "沙盒".encode("utf-8")
        container.next_exec = FakeExec(
            messages=[_msg(encoded[:2], 1), _msg(encoded[2:], 1)],
            inspect_results=[{"ExitCode": 0, "Running": False}],
        )
        result = await session.exec("whatever")
        assert result.output == "沙盒"

    async def test_output_capped_but_drained(self, session, fake_docker, monkeypatch):
        monkeypatch.setattr(config, "SANDBOX_MAX_OUTPUT_CHARS", 10)
        await session.ensure_container()
        container = fake_docker.created_containers[0]
        container.next_exec = FakeExec(
            messages=[_msg(b"a" * 8, 1), _msg(b"b" * 8, 1), _msg(b"c" * 8, 1)],
            inspect_results=[{"ExitCode": 0, "Running": False}],
        )
        result = await session.exec("whatever")
        assert result.output == "a" * 8 + "b" * 2
        assert result.truncated

    async def test_exit_code_polls_until_settled(self, session, fake_docker):
        await session.ensure_container()
        container = fake_docker.created_containers[0]
        container.next_exec = FakeExec(
            messages=[],
            inspect_results=[
                {"ExitCode": None, "Running": True},
                {"ExitCode": 7, "Running": False},
            ],
        )
        result = await session.exec("whatever")
        assert result.exit_code == 7

    async def test_abandon_guard_raises_timeout_error(self, session, fake_docker, monkeypatch):
        """exec 通道卡死 → asyncio 弃等护栏(只提前返回,不假装杀死了进程)。"""
        monkeypatch.setattr(config, "SANDBOX_COMMAND_TIMEOUT", 0)
        monkeypatch.setattr(
            "tools.builtin.sandbox_session.EXEC_ABANDON_GRACE_SEC", 0.05
        )
        await session.ensure_container()
        container = fake_docker.created_containers[0]

        class HangingStream(FakeStream):
            async def read_out(self):
                await asyncio.sleep(60)

        class HangingExec(FakeExec):
            def start(self, detach=False):
                return HangingStream([])

        container.next_exec = HangingExec([], [{"ExitCode": None, "Running": True}])
        with pytest.raises(SandboxExecTimeoutError):
            await session.exec("hang")
