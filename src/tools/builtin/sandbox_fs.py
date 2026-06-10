"""沙盒工作区的 host 侧文件系统访问 —— **唯一入口,纪律集中于此**。

为什么单独成模块(2026-06-10,五轮 review 后的架构收口):mount / persist /
watchdog(将来还有 C-reap)都要从 host 侧访问一个**不可信容器可并发修改**的
bind mount。两条纪律之前手写在每个调用点:

  1. **fd 钉住** —— 绝不按名字跨"检查→使用"间隙重解析路径。单次
     `open(abs, O_NOFOLLOW)` 只保护最终组件(内核逐组件解析,中间目录是 symlink
     照样跟过去);容器后台进程能在校验通过后把已验证的父目录换成指向池外的 link,
     宿主跟链读/写池外文件。修法是逐级 `openat(O_DIRECTORY|O_NOFOLLOW)` 持 fd ——
     fd 钉 inode 不钉名字,换名/换链动不了已持有的 fd。
  2. **fail-closed** —— 测不准就保守失败,绝不返回可能偏低的数让 watchdog 信任。
     枚举唯一良性错误 `ENOENT`(条目已被 rm,本不占空间),其余一切 OSError 都当
     "测不准"处理。

手写在每处的代价 = 每轮 review 只修一个点、漏掉兄弟点(路径 TOCTOU → 目录 TOCTOU
→ depth fail-open → EMFILE fail-open,五轮同根)。收成一个模块 = 纪律写一次、测
一次,新调用点想漏都漏不了。**业务代码不得再手写 os.walk/os.open/os.path 访问
工作区 —— 一律走这里。**

(Linux 5.6+ 的 `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` 可一发解决逐级
openat 做的事,但 dev mac 无;逐级 openat 是可移植等价物。)
"""

import errno
import os
import stat
from typing import List, Tuple

# 每个目录项(文件或目录)的最低计费 = 一个 ext4 块。块占用本身已含此量级,
# 但有些 fs(APFS / tmpfs)对空目录报 st_blocks=0,会留下"海量空目录/inode 耗尽
# 但 usage 不涨"的盲区。取 max(块占用, 此值)把每个条目锚到至少一个块 —— 既贴合
# 部署用的 ext4 池子真实成本,又让计量与 fs 的块上报方式无关,顺带把 inode 压力
# 折进同一个字节度量(无需独立 inode 旋钮)。
ENTRY_MIN_BYTES = 4096

# 计量遍历最大深度。fd 钉住的递归每层持一个目录 fd,而容器能极廉价地 mkdir -p 任意
# 深目录(每层 ≈ 一块)→ 不限会耗尽**整个 backend 进程**的 fd(比配额绕过更糟的
# DoS)。超此深度返回 incomplete=True,调用方 fail-closed。512 远超真实沙盒用途
# (解压工程 / 构建树深度通常 < 50),又把 fd 占用钉在安全范围。
MAX_WALK_DEPTH = 512


class WorkspaceEscape(Exception):
    """路径逃出工作区:词法 .|.. / 中间组件是 symlink / 叶子是 symlink /
    叶子非普通文件。调用方按"非法工作区路径"对模型报错。"""


class FileTooLarge(Exception):
    def __init__(self, size: int):
        self.size = size


def _benign(e: OSError) -> bool:
    """计量遍历里唯一良性的错误:条目已消失(容器自己 rm,内容确实不再占空间)。
    其余一切 = 测不准 = fail-closed。"""
    return e.errno == errno.ENOENT


def _walk_to_parent(workspace_dir: str, rel: str) -> Tuple[List[int], str]:
    """逐级 openat 走到 rel 的父目录;返回 (open_fds, leaf_name)。

    open_fds[-1] 是父目录 fd,调用方在 finally 里把列表全 close。词法先拒
    空 / .|..,中间 symlink 由 O_NOFOLLOW 拒(ELOOP)。targeted 访问(read/write)
    的错误一律向上抛 → 调用方转成 loud 的工具失败(无"少算"语义可言)。
    """
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise WorkspaceEscape(rel)
    # base 也 NOFOLLOW:容器够不着 scratch 根(只 bind 了 workspace/ 内部),
    # 但 base 换链的兜底没成本。
    fds = [os.open(workspace_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    try:
        for comp in parts[:-1]:
            try:
                fds.append(
                    os.open(
                        comp,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fds[-1],
                    )
                )
            except OSError as e:
                # 中间组件是 symlink(ELOOP)或被换成非目录(ENOTDIR)= 逃逸尝试,
                # 报清晰的"非法工作区路径"而非泛化 IO 失败。其余 OSError(权限等)透传。
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise WorkspaceEscape(rel) from e
                raise
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    return fds, parts[-1]


def write_file(workspace_dir: str, rel: str, data: bytes) -> None:
    """逐级 openat 写(同步,调用方 to_thread)。先摘旧叶子(摘掉容器可能植的
    symlink)再 O_CREAT|O_NOFOLLOW 新建;fchmod 绕 umask 授 0o666。

    fchmod 必要:os.open 的 mode 是**请求值**,内核实际授 `mode & ~umask` ——
    backend 以 umask 077 跑时文件会落 0600、容器内 uid 1000 读不了(mount 报成功、
    后续 bash 才 permission denied)。fchmod 是显式改权限、不经 umask。
    """
    fds, leaf = _walk_to_parent(workspace_dir, rel)
    try:
        parent = fds[-1]
        try:
            os.unlink(leaf, dir_fd=parent)
        except FileNotFoundError:
            pass
        fd = os.open(
            leaf,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o666,
            dir_fd=parent,
        )
        try:
            os.fchmod(fd, 0o666)
            os.write(fd, data)
        finally:
            os.close(fd)
    finally:
        for fd in fds:
            os.close(fd)


def read_file(workspace_dir: str, rel: str, max_bytes: int) -> bytes:
    """逐级 openat 读(同步,调用方 to_thread)。fstat 在 race-free 的 fd 上做
    类型/大小判别 —— 目录/缺失/超大都从这一次 fstat 出,无独立 lstat 解析窗口。

    叶子 symlink → ELOOP → WorkspaceEscape;目录 → IsADirectoryError;
    缺失 → FileNotFoundError;非普通文件 → WorkspaceEscape;超 max_bytes →
    FileTooLarge(读前拒,不把大文件物化进内存)。
    """
    fds, leaf = _walk_to_parent(workspace_dir, rel)
    try:
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise WorkspaceEscape(rel) from e
            raise
        try:
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(rel)
            if not stat.S_ISREG(st.st_mode):
                raise WorkspaceEscape(rel)
            # fstat 早拒诚实大文件(不必读、能报真实大小)。但 fstat 只看一眼,
            # 容器后台进程能在 fstat 后继续 append → 读循环必须**累计复核**,
            # 否则把超限内容读进内存(size guard 形同虚设)。读到 max_bytes+1
            # 即停并抛:内存占用钉死在 max_bytes+1,与文件实际涨多大无关。
            if st.st_size > max_bytes:
                raise FileTooLarge(st.st_size)
            limit = max_bytes + 1
            chunks: List[bytes] = []
            total = 0
            while total < limit:
                block = os.read(fd, min(1 << 20, limit - total))
                if not block:
                    break
                total += len(block)
                chunks.append(block)
            if total > max_bytes:
                raise FileTooLarge(total)  # 竞态 append:实际大小不可信,只报超限
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        for fd in fds:
            os.close(fd)


def _charge_blocks(st: os.stat_result) -> int:
    """一条 stat 结果的计费字节 = max(块占用 st_blocks×512, 每条目最低)。
    取不到块数时以表观大小代块占用。"""
    blocks = getattr(st, "st_blocks", None)
    usage = blocks * 512 if blocks is not None else st.st_size
    return max(usage, ENTRY_MIN_BYTES)


def measure_usage(root: str) -> Tuple[int, bool]:
    """目录树的计费占用 → (总字节, incomplete)。每条目 max(块占用, 一块)。

    watchdog 软配额按它计:小文件每个至少占一个 fs 块,表观大小会低估池子
    真实消耗(探针①实测 50k×100B 表观 4.8MB / 块占用 195MB,~40×)。
    **目录自身也是文件**(其内容是目录项,空目录在 ext4 也占一个块),海量空目录/
    深树能消耗块与 inode 而表观大小不涨 → 绕过 per-turn 配额只剩池子硬墙兜底。

    全程 fd 钉住(见模块 docstring)。每个目录项一律先 `entry.stat(follow_symlinks=
    False)`(fstatat,race-free)计费,只对 `S_ISDIR` 真实目录递归 —— 计量与条目类型
    解耦:文件 / 空目录 / symlink(指 dir 或 file,lstat 看链本体故 S_ISDIR 为 False,
    只计费不递归)/ fifo / 设备全走同一路径,无分类盲区。

    **incomplete=True = 测不准 → 调用方 fail-closed 当超额**:开不出 fd(EMFILE/
    ENFILE)、容器 chmod 000 藏子树(EACCES)、被换链(ELOOP/ENOTDIR)、深度超
    MAX_WALK_DEPTH —— 任一发生都置 True。原则:能完成计量就计,完成不了就保守杀,
    绝不返回偏低的数。唯一良性错误是 ENOENT(条目已被 rm,本不占空间)。
    """
    incomplete = False

    def walk(dir_fd: int, depth: int) -> int:
        nonlocal incomplete
        subtotal = 0
        try:
            scan = os.scandir(dir_fd)  # 不夺 fd 所有权,调用方仍负责 close(dir_fd)
        except OSError as e:
            if not _benign(e):
                incomplete = True  # 扫不动这棵子树 → 测不准 → fail-closed
            return 0
        with scan:
            for entry in scan:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    if not _benign(e):
                        incomplete = True
                    continue
                subtotal += _charge_blocks(st)
                if not stat.S_ISDIR(st.st_mode):
                    continue
                if depth >= MAX_WALK_DEPTH:
                    incomplete = True
                    continue  # 不再下探(防 fd 耗尽);incomplete → 调用方 fail-closed
                try:
                    child_fd = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=dir_fd,
                    )
                except OSError as e:
                    # 已被 rm → 跳过;EMFILE/EACCES/被换链等 → 测不准 → fail-closed
                    if not _benign(e):
                        incomplete = True
                    continue
                try:
                    subtotal += walk(child_fd, depth + 1)
                finally:
                    os.close(child_fd)
        return subtotal

    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        # 根不存在 = 本 turn 还没写过沙盒,usage 真为 0;其余(EMFILE/EACCES)= 测不准
        return 0, not _benign(e)
    try:
        try:
            total = _charge_blocks(os.fstat(root_fd))  # 根自身(无父目录代它计费)
        except OSError:
            total = 0
        total += walk(root_fd, 1)
    finally:
        os.close(root_fd)
    return total, incomplete
