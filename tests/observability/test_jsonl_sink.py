"""
JsonlSink 单元测试 — 验证写入、轮转、异常吞、formatter 不污染。
"""

import json
import logging

import pytest

from observability.jsonl_sink import JsonlSink


def test_write_single_line(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=2, mirror_stdout=False)
    sink.write({"hello": "world", "n": 42})
    sink.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj == {"hello": "world", "n": 42}


def test_writes_one_object_per_line(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=2, mirror_stdout=False)
    sink.write({"a": 1})
    sink.write({"b": 2})
    sink.write({"c": 3})
    sink.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}
    assert json.loads(lines[2]) == {"c": 3}


def test_formatter_only_message_no_timestamp_prefix(tmp_path):
    """jsonl 必须是纯 JSON 对象,不能有 '%(asctime)s [INFO]' 一类前缀。"""
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=2, mirror_stdout=False)
    sink.write({"k": "v"})
    sink.close()

    raw = path.read_text(encoding="utf-8").strip()
    # 一行必须直接以 `{` 开头(即首字符即 JSON 对象起始)
    assert raw.startswith("{")
    assert raw == json.dumps({"k": "v"}, ensure_ascii=False)


def test_rotation_when_exceeding_max_size(tmp_path):
    """写超过 max_mb,应产生 .1 备份。"""
    path = tmp_path / "events.jsonl"
    # 1MB ceiling,每行写 ~100 字节,需要约 10k 条;改用极小 max 加速
    # 但 max_mb 是整数。改用 backups=2 + 写大字符串至 > 1MB 即可。
    sink = JsonlSink(path, max_mb=1, backups=2, mirror_stdout=False)
    # 每个 obj 用 ~10KB 内容(避免上百次循环)
    big_payload = "x" * 10_000
    for i in range(200):
        sink.write({"i": i, "payload": big_payload})
    sink.close()

    # 主文件 + 至少 1 个备份
    backups = list(tmp_path.glob("events.jsonl*"))
    assert any(b.name.endswith(".1") for b in backups), f"expected rotation, got {backups}"


def test_logger_does_not_propagate_to_root(tmp_path):
    """sink 自己的 handler 不能污染根 logger / 主应用日志。"""
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=1, mirror_stdout=False)
    obs_logger = logging.getLogger(f"obs.{path.name}")
    assert obs_logger.propagate is False, "obs sink logger must not propagate"
    sink.close()


def test_write_swallows_exception(tmp_path):
    """如果传一个不可序列化的对象,write 也必须 swallow 不抛。"""
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=1, mirror_stdout=False)

    # 一个无法被 default=str 序列化的对象?其实 default=str 对几乎所有对象都给 str。
    # 用一个会在 json.dumps 抛 TypeError 的递归引用:
    cyclic: dict = {}
    cyclic["self"] = cyclic

    # 不应抛 — sink 必须吞
    sink.write(cyclic)
    sink.close()


def test_close_is_idempotent(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path, max_mb=1, backups=1, mirror_stdout=False)
    sink.write({"a": 1})
    sink.close()
    # 再调一次不抛
    sink.close()
