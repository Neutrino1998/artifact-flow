"""Phase C 心跳注册表 + 判色 + ERROR 计数 + marker 代报的单元测试。"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from observability.heartbeat import HeartbeatWriter
from observability import error_counter
from observability import admin_runtime
from utils.instance import INSTANCE_ID
from utils.time import utc_now


# ── Key 形状(单一真相源,读写两侧共用) ──

def test_instance_key_and_scan_roundtrip():
    assert HeartbeatWriter.instance_key("af", "web-1") == "{af:instance:web-1}"
    assert HeartbeatWriter.scan_pattern("af") == "{af:instance:*}"
    assert HeartbeatWriter.instance_id_from_key("{af:instance:web-1}") == "web-1"
    # 空前缀(默认 REDIS_KEY_PREFIX="")也要能反解
    assert HeartbeatWriter.instance_id_from_key("{:instance:web-1}") == "web-1"


# ── 写:redis=None no-op;有 redis 走单命令 SET+EX ──

async def test_write_noop_when_no_redis():
    hb = HeartbeatWriter(redis_client=None, key_prefix="af", ttl_sec=300)
    await hb.write({"ts": "x"})  # 不得抛


async def test_write_sets_key_with_ttl():
    class FakeRedis:
        def __init__(self):
            self.store = {}

        async def set(self, k, v, ex=None):
            self.store[k] = (v, ex)

    fr = FakeRedis()
    hb = HeartbeatWriter(redis_client=fr, key_prefix="af", ttl_sec=300)
    await hb.write({"ts": "2026-07-04T01:00:00", "process": {"rss_mb": 512}})
    key = HeartbeatWriter.instance_key("af", INSTANCE_ID)
    assert key in fr.store
    raw, ex = fr.store[key]
    assert ex == 300
    payload = json.loads(raw)
    assert payload["instance_id"] == INSTANCE_ID
    assert payload["process"]["rss_mb"] == 512
    assert payload["version"]  # 至少 "dev"


async def test_delete_removes_key():
    """#2 回归:优雅停机删本实例 key,不留幽灵红行。"""
    class FakeRedis:
        def __init__(self):
            self.store = {"{af:instance:" + INSTANCE_ID + "}": ("x", 300)}
            self.deleted = []

        async def set(self, k, v, ex=None):
            self.store[k] = (v, ex)

        async def delete(self, k):
            self.deleted.append(k)
            self.store.pop(k, None)

    fr = FakeRedis()
    hb = HeartbeatWriter(redis_client=fr, key_prefix="af", ttl_sec=300)
    await hb.delete()
    assert fr.deleted == [HeartbeatWriter.instance_key("af", INSTANCE_ID)]
    # redis=None 时 delete no-op(不抛)
    await HeartbeatWriter(redis_client=None, key_prefix="af", ttl_sec=300).delete()


async def test_write_swallows_redis_error():
    class BoomRedis:
        async def set(self, *a, **k):
            raise ConnectionError("down")

    hb = HeartbeatWriter(redis_client=BoomRedis(), key_prefix="af", ttl_sec=300)
    await hb.write({"ts": "x"})  # 吞掉,不冒泡


# ── payload 组装:含进程内计数/摘要字段 ──

def test_payload_includes_counter_and_wedge():
    class FakeCounter:
        count = 3
        last_ts = "2026-07-04T00:00:00"

    class FakeWD:
        def last_wedge(self):
            return {"ts": "2026-07-04T00:00:00", "lag_ms": 5100.0, "wedged": True}

    hb = HeartbeatWriter(
        redis_client=None, key_prefix="af", ttl_sec=300,
        error_counter=FakeCounter(), watchdog=FakeWD(),
    )
    p = hb.build_local_payload({"ts": "2026-07-04T01:00:00"})
    assert p["error_count"] == 3
    assert p["last_error_ts"] == "2026-07-04T00:00:00"
    assert p["last_wedge"]["lag_ms"] == 5100.0
    assert json.dumps(p)  # 必须可序列化


# ── marker 代报:按 INSTANCE_ID 过滤,取最近一条 + 累计次数 ──

def test_read_autoheal_marker_filters_by_instance(tmp_path):
    marker = tmp_path / "restart-marker.jsonl"
    marker.write_text(
        json.dumps({"ts": "2026-07-04T10:00:00", "instance_id": "other", "reason": "unhealthy"}) + "\n"
        + json.dumps({"ts": "2026-07-04T11:00:00", "instance_id": INSTANCE_ID, "reason": "unhealthy"}) + "\n"
        + json.dumps({"ts": "2026-07-04T12:00:00", "instance_id": INSTANCE_ID, "reason": "unhealthy"}) + "\n",
        encoding="utf-8",
    )
    hb = HeartbeatWriter(redis_client=None, key_prefix="af", ttl_sec=300,
                         autoheal_marker_path=str(marker))
    res = hb._read_autoheal_marker()
    assert res == {"ts": "2026-07-04T12:00:00", "reason": "unhealthy", "count": 2}


def test_read_autoheal_marker_absent_returns_none(tmp_path):
    hb = HeartbeatWriter(redis_client=None, key_prefix="af", ttl_sec=300,
                         autoheal_marker_path=str(tmp_path / "nope.jsonl"))
    assert hb._read_autoheal_marker() is None
    # 未配置路径
    hb2 = HeartbeatWriter(redis_client=None, key_prefix="af", ttl_sec=300)
    assert hb2._read_autoheal_marker() is None


# ── ERROR 计数 handler:只认 ERROR+,累计 + 时间戳 ──

def test_error_counter_counts_only_errors():
    error_counter.reset()
    try:
        h = error_counter.install()
        lg = logging.getLogger("ArtifactFlow")
        lg.warning("meh")
        assert h.count == 0
        lg.error("boom")
        assert h.count == 1 and h.last_ts is not None
        lg.critical("worse")
        assert h.count == 2
        # install 幂等:返回同一单例
        assert error_counter.install() is h
    finally:
        error_counter.reset()


# ── 判色八态 ──

def _status(**kw):
    now = utc_now()
    p = {"ts": now.isoformat()}
    p.update(kw)
    return admin_runtime._compute_status(p, now)


def _reason_codes(**kw):
    now = utc_now()
    p = {"ts": now.isoformat()}
    p.update(kw)
    return [r["code"] for r in admin_runtime._compute_status_reasons(p, now)]


def test_compute_status_matrix():
    now = utc_now()
    assert _status() == "green"
    assert admin_runtime._compute_status({"ts": (now - timedelta(seconds=120)).isoformat()}, now) == "red"
    assert admin_runtime._compute_status({"ts": None}, now) == "red"
    assert _status(loop_lag_ms={"max_1m_ms": 900}) == "yellow"
    assert _status(last_error_ts=(now - timedelta(seconds=60)).isoformat()) == "yellow"
    assert _status(last_error_ts=(now - timedelta(seconds=999)).isoformat()) == "green"
    assert _status(last_wedge={"ts": "x", "lag_ms": 5000}) == "yellow"
    assert _status(last_autoheal={"ts": (now - timedelta(seconds=30)).isoformat()}) == "yellow"


def test_compute_status_reasons_match_status_inputs():
    now = utc_now()
    assert _reason_codes() == []
    assert admin_runtime._compute_status_reasons({"ts": None}, now) == [
        {"code": "heartbeat_stale", "label": "心跳陈旧"}
    ]
    assert _reason_codes(
        loop_lag_ms={"max_1m_ms": 900},
        last_error_ts=(now - timedelta(seconds=60)).isoformat(),
        last_wedge={"ts": "x", "lag_ms": 5000},
        last_autoheal={"ts": (now - timedelta(seconds=30)).isoformat()},
    ) == ["loop_lag_warn", "recent_error", "wedge_seen", "autoheal_recent"]
