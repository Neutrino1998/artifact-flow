"""
JsonlSink — 轮转写盘 + stdout mirror 的 jsonl 写入器

设计要点(详见 docs/_archive/ops/incident-2026-05-14-fix-plan.md PR-obs-lite §持久化与循环写策略):

- 走 stdlib RotatingFileHandler 做大小循环,默认 50MB × 10 = 500MB 总占用,远高于
  实测每天 ~600 KB 的 metrics.jsonl 增长 → 单切片覆盖 ~80 天 / 总覆盖 ~800 天
- formatter 仅 %(message)s,不加时间戳前缀,保证 "一行一对象" jsonl 契约
- propagate=False,不污染根 logger 的 stdout/file handler(根 logger 已有自己的
  artifactflow.log 文件 handler,observer 输出走独立通道)
- stdout mirror 走单独的 StreamHandler,作为二级兜底(docker logs 拉得到,即使
  持久卷意外丢失也还有一份)
- 写入异常一律吞(observer 不能拖累 observee — observability 失败决不能让业务
  路径出错;对齐 CLAUDE.md 的 loud-failure 原则反向面:观测自身失败只能 swallow)
- 单进程同写假设 — backend 单进程,RotatingFileHandler 内置锁就够。日后切多
  worker / 多进程模式需重新设计(rotate 瞬间互相覆盖)
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
        mirror_stdout: bool = True,
    ):
        """
        Args:
            path: jsonl 写入路径(目录自动创建)
            max_mb: 单文件大小上限(MB),超即 rotate
            backups: 保留备份数(.1 ~ .N)
            mirror_stdout: 是否同步镜像到 stdout(docker logs 兜底通道)
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
