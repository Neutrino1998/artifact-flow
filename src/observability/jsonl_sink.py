"""
JsonlSink — 轮转写盘 + 可选 stdout mirror 的 jsonl 写入器

设计要点:
- 走 stdlib RotatingFileHandler 做大小循环;formatter 仅 %(message)s,
  保证 "一行一对象" 的 jsonl 契约,不加时间戳前缀
- propagate=False,不污染根 logger 的主应用日志流
- 主通道是持久卷上的 jsonl 文件;stdout mirror 默认关,作为
  "持久卷未挂载 / 挂错路径" 的二级兜底通道(docker logs 拉得到)
  按需通过 `mirror_stdout=True` 或 `OBS_STDOUT_MIRROR` 配置打开
- 写入异常一律吞 — observability 失败决不能拖累业务路径
- 单进程同写假设(backend 单进程,RotatingFileHandler 内置锁就够;
  日后切多 worker 需重新设计:rotate 瞬间会互相覆盖)
"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonlSink:
    """
    轮转 jsonl 写入器(可选 stdout mirror)

    用法:
        sink = JsonlSink(Path("data/observability/metrics.jsonl"), max_mb=50, backups=10)
        sink.write({"ts": "...", "loop_lag_ms": 18, ...})
    """

    def __init__(
        self,
        path: Path,
        max_mb: int,
        backups: int,
        mirror_stdout: bool = False,
    ):
        """
        Args:
            path: jsonl 写入路径(目录自动创建)
            max_mb: 单文件大小上限(MB),超即 rotate
            backups: 保留备份数(.1 ~ .N)
            mirror_stdout: 是否同步镜像到 stdout(默认 False;打开作为
                docker logs 兜底通道,代价是污染主应用日志流)
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # 独立 logger,handler 不进根 logger,避免污染主日志流
        self._log = logging.getLogger(f"obs.{path.name}")
        self._log.handlers = []
        self._log.setLevel(logging.INFO)
        self._log.propagate = False

        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_mb * 1024 * 1024,
            backupCount=backups,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        self._log.addHandler(file_handler)

        if mirror_stdout:
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setFormatter(logging.Formatter(f"[obs.{path.stem}] %(message)s"))
            self._log.addHandler(stdout_handler)

        self.path = path

    def write(self, obj: dict) -> None:
        """写一行 jsonl。失败一律吞,不抛(observer 不能拖累 observee)。"""
        try:
            line = json.dumps(obj, ensure_ascii=False, default=str)
            self._log.info(line)
        except Exception:
            pass

    def close(self) -> None:
        """关闭所有 handler(测试 / 生命周期收尾用)。"""
        for h in list(self._log.handlers):
            try:
                h.close()
            except Exception:
                pass
            self._log.removeHandler(h)
