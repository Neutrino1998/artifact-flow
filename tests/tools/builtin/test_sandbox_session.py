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
    _dir_usage_bytes,
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
        # workspace/tmp 子目录布局(双 bind 源)+ HOME 预建(部分工具不自建)
        assert os.path.isdir(session.workspace_dir)
        assert os.path.isdir(session.tmp_dir)
        assert os.path.isdir(os.path.join(session.tmp_dir, "home"))
        # 容器内 uid 1000 须可写(D 阶段在真实 Linux 上复验属主策略)
        for d in (session.scratch_dir, session.workspace_dir, session.tmp_dir):
            assert os.stat(d).st_mode & 0o777 == 0o777

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
        # 双 bind:workspace + /tmp 入池;rootfs 只读堵 overlay upper 无界写
        assert host["Binds"] == [
            f"{session.workspace_dir}:{WORKSPACE_MOUNT}:rw",
            f"{session.tmp_dir}:/tmp:rw",
        ]
        assert host["ReadonlyRootfs"] is True
        # HOME/缓存写点重定向进 /tmp(探针③:matplotlib 否则降级+警告)
        assert "HOME=/tmp/home" in cfg["Env"]
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

    async def test_container_died_mid_exec_is_loud_and_sticky(self, session, fake_docker):
        """容器中途消失(外力 rm 等,无 sticky 前因)→ loud-fail + 后续不再撞死手柄。"""
        await session.ensure_container()
        container = fake_docker.created_containers[0]

        class DeadExec(FakeExec):
            def start(self, detach=False):
                raise DockerError(409, {"message": "container is not running"})

        container.next_exec = DeadExec([], [{"ExitCode": None, "Running": True}])
        with pytest.raises(SandboxUnavailableError, match="died"):
            await session.exec("whatever")
        with pytest.raises(SandboxUnavailableError, match="died"):
            await session.exec("again")


# ============================================================
# 磁盘配额(C′ 软配额层;loop 池子硬墙是部署侧,D 段验)
# ============================================================


class TestDirUsage:

    def test_counts_file_block_usage(self, tmp_path):
        (tmp_path / "f").write_bytes(b"x" * 100)
        # 块占用 ≥ 表观大小(小文件占整块)
        assert _dir_usage_bytes(str(tmp_path)) >= 100

    def test_empty_dirs_are_counted(self, tmp_path):
        """海量空目录也消耗块/inode —— 不计入 = 绕过 per-turn 配额(P2)。"""
        for i in range(200):
            (tmp_path / f"d{i}").mkdir()
        usage = _dir_usage_bytes(str(tmp_path))
        assert usage > 0  # 旧实现(只数 filenames)这里会是 0

    def test_symlink_dirs_are_counted_not_followed(self, tmp_path):
        """指向目录的 symlink 也耗 inode/目录项 —— os.walk 把它丢进 dirnames 既不
        递归也不计;scandir 统一 lstat 计费(P2 第 2 轮,弃 os.walk 的盲区)。"""
        target = tmp_path / "real_target"
        target.mkdir()
        (target / "big.bin").write_bytes(b"x" * 100_000)  # 链不该被跟进去计这块
        links = tmp_path / "links"
        links.mkdir()
        for i in range(100):
            (links / f"l{i}").symlink_to(target, target_is_directory=True)

        usage = _dir_usage_bytes(str(links))
        # 100 条 symlink 各计一块(≈400KB),且**没有**跟链把 100KB 的 target 内容
        # 重复计进来(否则会暴涨)
        assert usage >= 100 * 4096
        assert usage < 100 * 4096 + 50_000

    def test_missing_entries_skipped(self, tmp_path):
        # 不存在的路径不抛(容器并发增删)
        assert _dir_usage_bytes(str(tmp_path / "nope")) == 0

    def test_nested_real_dirs_recursed(self, tmp_path):
        """真实深目录树正常递归计费(scandir 不误伤合法深路径)。"""
        d = tmp_path
        for level in range(5):
            d = d / f"lvl{level}"
            d.mkdir()
        (d / "leaf.txt").write_bytes(b"y" * 100)
        usage = _dir_usage_bytes(str(tmp_path))
        # 根 + 5 层目录 + 1 文件 = 7 条目,各至少一块
        assert usage >= 7 * 4096


class TestQuota:

    async def test_pool_admission_refuses_when_low(self, session, fake_docker, monkeypatch):
        """准入水位:池子剩余低于阈值 → 拒起容器,sticky,不打 daemon。"""
        monkeypatch.setattr(config, "SANDBOX_POOL_MIN_FREE_MB", 10 ** 9)  # 1PB,必触发
        with pytest.raises(SandboxUnavailableError, match="storage"):
            await session.exec("echo hi")
        with pytest.raises(SandboxUnavailableError, match="storage"):
            await session.exec("echo hi")
        assert fake_docker.create_calls == []

    async def test_watchdog_kills_over_quota_and_failure_is_sticky(
        self, session, fake_docker, monkeypatch
    ):
        monkeypatch.setattr(config, "SANDBOX_WORKSPACE_QUOTA_MB", 0)  # 任何写入即超额
        monkeypatch.setattr(config, "SANDBOX_WATCHDOG_INTERVAL_SEC", 0.01)
        await session.ensure_container()
        with open(os.path.join(session.workspace_dir, "blob.bin"), "wb") as f:
            f.write(b"x" * 4096)

        for _ in range(100):  # watchdog 异步触发,有界等待
            await asyncio.sleep(0.02)
            if not session.started:
                break
        assert fake_docker.created_containers[0].deleted_with == {"force": True}
        with pytest.raises(SandboxUnavailableError, match="quota"):
            await session.exec("echo hi")

    async def test_inflight_exec_attributed_to_quota_kill(
        self, session, fake_docker, monkeypatch
    ):
        """探针②:watchdog 杀容器时 in-flight exec 正常返回 exit=137 —— 裸 137
        会被误读,sticky 已置时必须按配额失败归因。"""
        monkeypatch.setattr(config, "SANDBOX_WORKSPACE_QUOTA_MB", 0)
        monkeypatch.setattr(config, "SANDBOX_WATCHDOG_INTERVAL_SEC", 0.01)
        await session.ensure_container()
        container = fake_docker.created_containers[0]
        with open(os.path.join(session.workspace_dir, "blob.bin"), "wb") as f:
            f.write(b"x" * 4096)

        class SlowStream(FakeStream):
            async def read_out(self):
                await asyncio.sleep(0.2)  # 给 watchdog 时间触发
                return None

        class SlowExec(FakeExec):
            def start(self, detach=False):
                return SlowStream([])

        container.next_exec = SlowExec([], [{"ExitCode": 137, "Running": False}])
        with pytest.raises(SandboxUnavailableError, match="quota"):
            await session.exec("dd if=/dev/zero of=big")

    async def test_close_cancels_watchdog(self, session):
        await session.ensure_container()
        watchdog = session._watchdog_task
        assert watchdog is not None and not watchdog.done()
        await session.close()
        assert watchdog.cancelled()
        assert session._watchdog_task is None
