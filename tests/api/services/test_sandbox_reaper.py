"""SandboxReaper 单测 —— fake aiodocker client + fake store + tmp scratch 根。

验回收谓词(per-turn 差集、grace、namespace 隔离)、双源枚举(容器 + scratch 目录)、
幂等(404 / FileNotFoundError 当成功)、tick 不被单跳异常杀死。真 daemon SIGKILL 路径
归 tests/manual 矩阵。
"""

import os
import time

import pytest
from aiodocker.exceptions import DockerError

from api.main import _should_start_reaper
from api.services.sandbox_reaper import SandboxReaper
from tools.builtin.sandbox_session import (
    LABEL_CONVERSATION,
    LABEL_MESSAGE,
    LABEL_NAMESPACE,
    SANDBOX_LABEL,
    scratch_dir_name,
)


# ----------------------------- fakes -----------------------------


class FakeContainer:
    """仿 aiodocker DockerContainer:_container dict + _id + __getitem__ + delete。"""

    def __init__(self, cid, conv, msg, namespace="default", created=None, delete_error=None):
        self._id = cid
        self._container = {
            "Id": cid,
            "Created": created if created is not None else (time.time() - 10_000),
            "Labels": {
                SANDBOX_LABEL: "1",
                LABEL_NAMESPACE: namespace,
                LABEL_CONVERSATION: conv,
                LABEL_MESSAGE: msg,
            },
        }
        self._delete_error = delete_error
        self.deleted = False

    def __getitem__(self, key):
        return self._container[key]

    async def delete(self, force=False):
        if self._delete_error is not None:
            raise self._delete_error
        self.deleted = True


class FakeDockerContainers:
    def __init__(self, containers):
        self._containers = containers

    async def list(self, all=False, filters=None):
        # reaper 总传 namespace label;这里不做服务端过滤,fake 直接全返,
        # 让 reaper 的 label 反解 + 谓词逻辑自己跑(namespace 测试单独构造)。
        return list(self._containers)


class FakeDocker:
    def __init__(self, containers):
        self.containers = FakeDockerContainers(containers)
        self.closed = False

    async def close(self):
        self.closed = True


class FakeStore:
    def __init__(self, active, is_shared=False):
        self._active = active  # {conv: msg}
        self.is_shared = is_shared

    async def list_active_executions(self):
        return dict(self._active)


def _mk_reaper(tmp_path, containers, active, *, namespace="default", grace=60, is_shared=False):
    docker = FakeDocker(containers)
    reaper = SandboxReaper(
        FakeStore(active, is_shared=is_shared),
        scratch_root=str(tmp_path),
        namespace=namespace,
        interval_sec=1,
        grace_sec=grace,
        docker_factory=lambda: docker,
    )
    reaper._docker = docker  # start() 不跑,直接注入
    return reaper, docker


def _mk_scratch(tmp_path, conv, msg, *, age_sec=10_000):
    d = tmp_path / scratch_dir_name(conv, msg)
    d.mkdir()
    (d / "workspace").mkdir()
    if age_sec:
        old = time.time() - age_sec
        os.utime(d, (old, old))
    return d


# ----------------------------- tests -----------------------------


class TestReapPredicate:

    async def test_orphan_container_and_dir_reaped(self, tmp_path):
        c = FakeContainer("abc123", "conv-1", "msg-1")
        _mk_scratch(tmp_path, "conv-1", "msg-1")
        reaper, _ = _mk_reaper(tmp_path, [c], active={})  # 无活跃 = 全孤儿
        stats = await reaper.reap_once()
        assert c.deleted
        assert stats.containers_reaped == 1
        assert stats.dirs_reaped == 1
        assert not (tmp_path / scratch_dir_name("conv-1", "msg-1")).exists()

    async def test_active_turn_not_reaped(self, tmp_path):
        c = FakeContainer("abc123", "conv-1", "msg-1")
        d = _mk_scratch(tmp_path, "conv-1", "msg-1")
        reaper, _ = _mk_reaper(tmp_path, [c], active={"conv-1": "msg-1"})
        stats = await reaper.reap_once()
        assert not c.deleted
        assert stats.containers_reaped == 0
        assert stats.dirs_reaped == 0
        assert d.exists()

    async def test_per_turn_old_turn_reaped_while_conv_active(self, tmp_path):
        """同会话新 turn 持活 lease,上一 turn 的漏拆孤儿仍须回收(per-turn 粒度)。"""
        old = FakeContainer("old", "conv-1", "msg-OLD")
        new = FakeContainer("new", "conv-1", "msg-NEW")
        _mk_scratch(tmp_path, "conv-1", "msg-OLD")
        _mk_scratch(tmp_path, "conv-1", "msg-NEW")
        reaper, _ = _mk_reaper(tmp_path, [old, new], active={"conv-1": "msg-NEW"})
        stats = await reaper.reap_once()
        assert old.deleted and not new.deleted
        assert stats.containers_reaped == 1
        assert not (tmp_path / scratch_dir_name("conv-1", "msg-OLD")).exists()
        assert (tmp_path / scratch_dir_name("conv-1", "msg-NEW")).exists()

    async def test_young_resource_within_grace_skipped(self, tmp_path):
        """刚建(< grace)且无活跃 lease:本轮不动,躲可见性差一拍误杀。"""
        c = FakeContainer("fresh", "conv-1", "msg-1", created=time.time())
        _mk_scratch(tmp_path, "conv-1", "msg-1", age_sec=0)
        reaper, _ = _mk_reaper(tmp_path, [c], active={}, grace=60)
        stats = await reaper.reap_once()
        assert not c.deleted
        assert stats.containers_reaped == 0
        assert stats.dirs_reaped == 0
        assert stats.skipped_young >= 2

    async def test_namespace_isolation(self, tmp_path):
        """别的部署(不同 namespace label)的容器不被本 reaper 认领。"""
        other = FakeContainer("other", "conv-X", "msg-X", namespace="other-deploy")
        reaper, _ = _mk_reaper(tmp_path, [other], active={}, namespace="default")
        stats = await reaper.reap_once()
        assert not other.deleted
        assert stats.containers_reaped == 0

    async def test_container_without_dir_reaped(self, tmp_path):
        """容器在、目录已删(close 删了目录但容器删失败):容器源独立回收。"""
        c = FakeContainer("abc", "conv-1", "msg-1")
        reaper, _ = _mk_reaper(tmp_path, [c], active={})
        stats = await reaper.reap_once()
        assert c.deleted
        assert stats.containers_reaped == 1
        assert stats.dirs_reaped == 0

    async def test_dir_without_container_reaped(self, tmp_path):
        """目录在、容器没了(--rm 自删 / daemon 重启):scratch 源独立回收。"""
        _mk_scratch(tmp_path, "conv-1", "msg-1")
        reaper, _ = _mk_reaper(tmp_path, [], active={})
        stats = await reaper.reap_once()
        assert stats.dirs_reaped == 1
        assert not (tmp_path / scratch_dir_name("conv-1", "msg-1")).exists()

    async def test_unrelated_dir_name_ignored(self, tmp_path):
        """scratch 根里非 {conv}__{msg} 命名的目录(或单文件)不碰。"""
        (tmp_path / "not-ours").mkdir()
        (tmp_path / "stray-file").write_text("x")
        reaper, _ = _mk_reaper(tmp_path, [], active={})
        stats = await reaper.reap_once()
        assert stats.dirs_reaped == 0
        assert (tmp_path / "not-ours").exists()


class TestIdempotenceAndResilience:

    async def test_container_404_counts_as_reaped(self, tmp_path):
        """别的 worker 已删 → 404 当幂等成功,不刷 error。"""
        c = FakeContainer("gone", "conv-1", "msg-1",
                          delete_error=DockerError(404, {"message": "No such container"}))
        reaper, _ = _mk_reaper(tmp_path, [c], active={})
        stats = await reaper.reap_once()
        assert stats.containers_reaped == 1

    async def test_container_delete_error_not_counted(self, tmp_path):
        """非 404 删失败:不计回收,留下个 tick 重试(不抛出杀循环)。"""
        c = FakeContainer("stuck", "conv-1", "msg-1",
                          delete_error=DockerError(500, {"message": "daemon busy"}))
        reaper, _ = _mk_reaper(tmp_path, [c], active={})
        stats = await reaper.reap_once()
        assert stats.containers_reaped == 0

    async def test_missing_scratch_root_is_noop(self, tmp_path):
        reaper, _ = _mk_reaper(tmp_path / "does-not-exist", [], active={})
        stats = await reaper.reap_once()
        assert stats.dirs_seen == 0
        assert stats.dirs_reaped == 0

    async def test_final_sweep_ignores_grace_under_local_store(self, tmp_path):
        """进程本地 store 的 final_sweep:runner 已停 = 无在途 turn,grace 内的新鲜
        残留也收(否则单副本停机漏拆的孤儿要等下次启动)。"""
        c = FakeContainer("fresh", "conv-1", "msg-1", created=time.time())
        _mk_scratch(tmp_path, "conv-1", "msg-1", age_sec=0)
        reaper, _ = _mk_reaper(tmp_path, [c], active={}, grace=60, is_shared=False)
        stats = await reaper.final_sweep()
        assert c.deleted
        assert stats.containers_reaped == 1 and stats.dirs_reaped == 1
        assert stats.skipped_young == 0

    async def test_final_sweep_keeps_grace_under_shared_store(self, tmp_path):
        """共享 store(多 worker)的 final_sweep:兄弟进程可能正起新 turn,grace 内的
        新鲜资源不能误删 —— 保留 grace。"""
        c = FakeContainer("fresh", "conv-1", "msg-1", created=time.time())
        _mk_scratch(tmp_path, "conv-1", "msg-1", age_sec=0)
        reaper, _ = _mk_reaper(tmp_path, [c], active={}, grace=60, is_shared=True)
        stats = await reaper.final_sweep()
        assert not c.deleted
        assert stats.containers_reaped == 0
        assert stats.skipped_young >= 2

    async def test_label_incomplete_container_treated_inactive(self, tmp_path):
        """label 残缺(无 conv/msg)的容器按不活跃处置,但仍在 grace 外才删。"""
        c = FakeContainer("weird", None, None)
        c._container["Labels"] = {SANDBOX_LABEL: "1", LABEL_NAMESPACE: "default"}
        reaper, _ = _mk_reaper(tmp_path, [c], active={"conv-1": "msg-1"})
        stats = await reaper.reap_once()
        # conv/msg 缺失 → _is_active=False → grace 外 → 回收(它本不该带我们的 label 却没归属)
        assert c.deleted
        assert stats.containers_reaped == 1


class TestReaperGate:
    """_should_start_reaper:破坏性默认关、共享 store 自动开、本地 store opt-in。"""

    def test_disabled_flag_wins(self):
        start, _ = _should_start_reaper(
            enabled=False, store_is_shared=True, allow_local_store=True
        )
        assert not start

    def test_shared_store_starts(self):
        start, reason = _should_start_reaper(
            enabled=True, store_is_shared=True, allow_local_store=False
        )
        assert start and "shared" in reason

    def test_local_store_off_by_default(self):
        """InMemory 默认不起 —— 破坏性误删的安全默认。"""
        start, reason = _should_start_reaper(
            enabled=True, store_is_shared=False, allow_local_store=False
        )
        assert not start
        assert "Redis" in reason or "SANDBOX_REAP_ALLOW_LOCAL_STORE" in reason

    def test_local_store_opt_in(self):
        start, reason = _should_start_reaper(
            enabled=True, store_is_shared=False, allow_local_store=True
        )
        assert start and "single-worker" in reason
