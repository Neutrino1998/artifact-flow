"""sandbox_fs —— host 侧工作区 FS 访问的纪律收口模块单测。

集中验证 fd 钉住(不按名字重解析路径)+ fail-closed(测不准就保守)这两条纪律,
覆盖 read_file / write_file(persist/mount 底层)与 measure_usage(watchdog 底层)。
工具层与会话层的集成行为分别在 test_sandbox_stage_tools.py / test_sandbox_session.py。
"""

import errno
import os
import resource
import tempfile

import pytest

from tools.builtin import sandbox_fs
from tools.builtin.sandbox_fs import (
    FileTooLarge,
    WorkspaceEscape,
    measure_usage,
    read_file,
    write_file,
)


# ============================================================
# measure_usage —— 计量(fd 钉住 + fail-closed)
# ============================================================


class TestMeasureUsage:

    def test_counts_file_block_usage(self, tmp_path):
        (tmp_path / "f").write_bytes(b"x" * 100)
        usage, incomplete = measure_usage(str(tmp_path))
        assert usage >= 100  # 块占用 ≥ 表观大小(小文件占整块)
        assert not incomplete

    def test_empty_dirs_are_counted(self, tmp_path):
        """海量空目录也消耗块/inode —— 不计入 = 绕过 per-turn 配额。"""
        for i in range(200):
            (tmp_path / f"d{i}").mkdir()
        usage, _ = measure_usage(str(tmp_path))
        assert usage > 0  # 旧 os.walk 实现(只数 filenames)这里会是 0

    def test_symlink_dirs_are_counted_not_followed(self, tmp_path):
        """指向目录的 symlink 也耗 inode/目录项,但绝不跟链进去重复计目标内容。"""
        target = tmp_path / "real_target"
        target.mkdir()
        (target / "big.bin").write_bytes(b"x" * 100_000)
        links = tmp_path / "links"
        links.mkdir()
        for i in range(100):
            (links / f"l{i}").symlink_to(target, target_is_directory=True)

        usage, _ = measure_usage(str(links))
        assert usage >= 100 * 4096
        assert usage < 100 * 4096 + 50_000

    def test_missing_root_is_zero_not_incomplete(self, tmp_path):
        # 根不存在 = 本 turn 没写过沙盒,usage 真为 0、非 fail-closed
        assert measure_usage(str(tmp_path / "nope")) == (0, False)

    def test_nested_real_dirs_recursed(self, tmp_path):
        d = tmp_path
        for level in range(5):
            d = d / f"lvl{level}"
            d.mkdir()
        (d / "leaf.txt").write_bytes(b"y" * 100)
        usage, incomplete = measure_usage(str(tmp_path))
        assert usage >= 7 * 4096  # 根 + 5 层目录 + 1 文件
        assert not incomplete

    def test_recursion_does_not_follow_symlinked_subdir(self, tmp_path):
        """子目录是 symlink 指池外 → openat O_NOFOLLOW 拒下探,不跟链遍历宿主。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        for i in range(50):
            (outside / f"host_file_{i}").write_bytes(b"z" * 100_000)  # 5MB
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "link").symlink_to(outside, target_is_directory=True)
        usage, _ = measure_usage(str(ws))
        assert usage < 50_000  # 绝不把 outside 的 5MB 算进来

    def test_depth_cap_signals_incomplete(self, tmp_path, monkeypatch):
        """超深树:返回 incomplete=True(调用方 fail-closed),不静默少算。"""
        monkeypatch.setattr(sandbox_fs, "MAX_WALK_DEPTH", 3)
        d = tmp_path
        for level in range(10):
            d = d / f"l{level}"
            d.mkdir()
        (d / "deep.txt").write_bytes(b"x" * 100)
        usage, incomplete = measure_usage(str(tmp_path))
        assert incomplete
        assert usage > 0

    def test_depth_within_cap_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_fs, "MAX_WALK_DEPTH", 20)
        d = tmp_path
        for level in range(5):
            d = d / f"l{level}"
            d.mkdir()
        _, incomplete = measure_usage(str(tmp_path))
        assert not incomplete

    def test_openat_failure_fails_closed(self, tmp_path, monkeypatch):
        """openat 下探失败(EMFILE/EACCES 等非 ENOENT)→ incomplete=True。
        reviewer 复现:RLIMIT_NOFILE=64 扫 80 层树曾返回 incomplete=False。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f").write_bytes(b"x" * 100)
        real_open = os.open

        def emfile_on_subdir(path, flags, *a, **k):
            if k.get("dir_fd") is not None and path == "sub":
                raise OSError(errno.EMFILE, "Too many open files")
            return real_open(path, flags, *a, **k)

        monkeypatch.setattr(os, "open", emfile_on_subdir)
        _, incomplete = measure_usage(str(tmp_path))
        assert incomplete

    def test_vanished_entry_is_benign(self, tmp_path, monkeypatch):
        """条目枚举后被 rm(ENOENT)是良性 —— 内容已不占空间,不触发 fail-closed。"""
        (tmp_path / "gone").mkdir()
        real_open = os.open

        def enoent_on_subdir(path, flags, *a, **k):
            if k.get("dir_fd") is not None and path == "gone":
                raise OSError(errno.ENOENT, "No such file or directory")
            return real_open(path, flags, *a, **k)

        monkeypatch.setattr(os, "open", enoent_on_subdir)
        _, incomplete = measure_usage(str(tmp_path))
        assert not incomplete

    def test_real_fd_exhaustion_fails_closed(self, tmp_path):
        """真降 RLIMIT_NOFILE 扫深树(reviewer 原始复现):必须 incomplete=True。"""
        d = tmp_path
        for i in range(80):
            d = d / f"l{i}"
            d.mkdir()
        (d / "deep_big").write_bytes(b"z" * 100_000)
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
        try:
            _, incomplete = measure_usage(str(tmp_path))
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        assert incomplete


# ============================================================
# read_file —— persist 底层(累计上限 + 类型判别)
# ============================================================


class TestReadFile:

    def test_honest_small_file_reads_fully(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "f.txt").write_bytes(b"hello")
        assert read_file(str(ws), "f.txt", 1024) == b"hello"

    def test_read_loop_caps_when_fstat_lies(self, tmp_path, monkeypatch):
        """fstat 报小、文件实大(模拟读前被 append):读循环累计复核必须拦下。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "big.bin").write_bytes(b"x" * 10_000)
        real_fstat = os.fstat

        def lying_fstat(fd):
            st = real_fstat(fd)
            return os.stat_result(
                (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                 st.st_gid, 4, st.st_atime, st.st_mtime, st.st_ctime)
            )

        monkeypatch.setattr(os, "fstat", lying_fstat)
        with pytest.raises(FileTooLarge):
            read_file(str(ws), "big.bin", 4)

    def test_at_limit_exact_ok(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "f").write_bytes(b"abcd")
        assert read_file(str(ws), "f", 4) == b"abcd"

    def test_dotdot_rejected(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(WorkspaceEscape):
            read_file(str(ws), "../escape", 1024)

    def test_parent_symlink_rejected(self, tmp_path):
        """中间组件是 symlink 指池外 → WorkspaceEscape(逐级 openat 的核心)。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_text("host secret")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "d").symlink_to(outside, target_is_directory=True)
        with pytest.raises(WorkspaceEscape):
            read_file(str(ws), "d/secret", 1024)

    def test_leaf_symlink_rejected(self, tmp_path):
        outside = tmp_path / "secret"
        outside.write_text("leak")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "innocent").symlink_to(outside)
        with pytest.raises(WorkspaceEscape):
            read_file(str(ws), "innocent", 1024)

    def test_directory_is_isadirectory(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "sub").mkdir()
        with pytest.raises(IsADirectoryError):
            read_file(str(ws), "sub", 1024)

    def test_missing_is_filenotfound(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(FileNotFoundError):
            read_file(str(ws), "nope", 1024)


# ============================================================
# write_file —— mount 底层(unlink+NOFOLLOW + fchmod 绕 umask)
# ============================================================


class TestWriteFile:

    def test_writes_and_reads_back(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        write_file(str(ws), "a.txt", b"content")
        assert (ws / "a.txt").read_bytes() == b"content"

    def test_fchmod_bypasses_umask(self, tmp_path):
        """umask 077 下仍落 0o666(容器 uid 1000 要读得到)。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        old = os.umask(0o077)
        try:
            write_file(str(ws), "a.txt", b"x")
            assert os.stat(ws / "a.txt").st_mode & 0o777 == 0o666
        finally:
            os.umask(old)

    def test_overwrites_planted_symlink_without_following(self, tmp_path):
        """叶子被植成指向池外的 symlink → unlink 摘链 + O_NOFOLLOW 新建,不写穿。"""
        outside = tmp_path / "outside.txt"
        outside.write_text("untouched")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "a.txt").symlink_to(outside)
        write_file(str(ws), "a.txt", b"new")
        assert outside.read_text() == "untouched"  # 池外没被改
        assert (ws / "a.txt").read_bytes() == b"new"

    def test_short_write_is_completed(self, tmp_path, monkeypatch):
        """os.write 短写(POSIX 允许)→ 循环写完,文件不被静默截断。
        reviewer 复现:monkeypatch 让 os.write 每次只吐 3 字节。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        real_write = os.write

        def short_write(fd, buf):
            return real_write(fd, bytes(buf)[:3])

        monkeypatch.setattr(os, "write", short_write)
        payload = b"abcdefghijklmnopqrstuvwxyz"
        write_file(str(ws), "a.txt", payload)
        assert (ws / "a.txt").read_bytes() == payload

    def test_zero_write_fails_loud(self, tmp_path, monkeypatch):
        """非空 buffer 却写入 0 字节(磁盘满 / 内核异常)→ loud-fail,不空转。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(os, "write", lambda fd, buf: 0)
        with pytest.raises(OSError):
            write_file(str(ws), "a.txt", b"data")

    def test_dotdot_rejected(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(WorkspaceEscape):
            write_file(str(ws), "../escape", b"x")
