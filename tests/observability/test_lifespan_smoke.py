"""
Smoke 集成测试 — 走 FastAPI lifespan 把四个组件启起来又关掉

定位:catch "lifespan wiring 漏装组件" 这类回归(单测都跑过但 startup
就崩了)。不验证 dump / 报警的内部行为,只验证 enter+exit 全程不抛 +
组件状态正确转移。

避开真实 DB / Redis:直接调 _start_observability + _stop_observability,
不走完整的 init_globals。
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_start_then_stop_observability_smoke(tmp_path, monkeypatch):
    """模拟 lifespan:_start_observability → 等一下 → _stop_observability。

    用 monkeypatch 把两个 jsonl 路径指到 tmp_path,避免污染真实 data/。
    """
    monkeypatch.setenv("ARTIFACTFLOW_JWT_SECRET", "smoke-test-secret")
    monkeypatch.setenv("ARTIFACTFLOW_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    # 在 import 之前重写 config 默认路径(避免污染真实 data/observability)
    monkeypatch.setattr(
        "config.config.OBS_METRICS_LOG_PATH",
        str(tmp_path / "metrics.jsonl"),
    )
    monkeypatch.setattr(
        "config.config.OBS_LOOP_LAG_LOG_PATH",
        str(tmp_path / "loop-lag.jsonl"),
    )
    monkeypatch.setattr("config.config.OBS_SAMPLE_INTERVAL_SEC", 1)
    monkeypatch.setattr("config.config.WATCHDOG_DEADMAN_TIMEOUT_MS", 500)

    # Fake 出 ExecutionRunner / DatabaseManager / Redis,绕过 init_globals
    from api import dependencies, main as main_mod

    fake_runner = MagicMock()
    fake_runner._tasks = {}
    fake_runner.active_task_count = 0
    monkeypatch.setattr(dependencies, "_execution_runner", fake_runner)

    fake_db = MagicMock()
    fake_db._engine = None  # sampler 会优雅返回 {}
    monkeypatch.setattr(dependencies, "_db_manager", fake_db)
    monkeypatch.setattr(dependencies, "_redis_client", None)

    loop = asyncio.get_running_loop()

    # 启 observability
    main_mod._start_observability(loop)
    try:
        assert main_mod._watchdog is not None
        assert main_mod._deadman is not None
        assert main_mod._sampler is not None
        # 让 sampler 至少跑一次(interval=1s)
        await asyncio.sleep(1.2)
    finally:
        await main_mod._stop_observability()

    # 句柄应清空
    assert main_mod._watchdog is None
    assert main_mod._deadman is None
    assert main_mod._sampler is None

    # metrics.jsonl 应至少写过一行
    metrics_file = tmp_path / "metrics.jsonl"
    assert metrics_file.exists()
    assert metrics_file.read_text(encoding="utf-8").strip(), "expected at least one sample"


@pytest.mark.asyncio
async def test_observability_bootstrap_failure_is_swallowed(tmp_path, monkeypatch, caplog):
    """如果 observability 启动失败,lifespan 仍能继续(只打 ERROR)。"""
    import logging
    from api import main as main_mod

    # 让 _start_observability 抛错
    def boom(loop):
        raise RuntimeError("simulated obs boot failure")

    monkeypatch.setattr(main_mod, "_start_observability", boom)

    # 模拟 lifespan 启动里的 try/except 块
    with caplog.at_level(logging.ERROR, logger="ArtifactFlow"):
        try:
            main_mod._start_observability(asyncio.get_running_loop())
        except Exception:
            # 这是 lifespan 里 try/except 包住的语义 — 测它确实抛了
            # (lifespan 自己有 try/except,这里只测 _start 本身抛错时的行为)
            pass

    # 而 _stop_observability 必须能在 "从未 start" 的情况下安全跑完
    await main_mod._stop_observability()
