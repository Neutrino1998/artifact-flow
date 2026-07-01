"""resolve_skill_activation — 用户按钮激活 slug 解析(C-3 reviewer round-2)。

两件正交的事:注入集(请求内去重、**不**按 parent 去重 → 重勾重注入)+ sticky 名单
(parent ∪ 注入 去重)。可见性 gate 在此;空 body gate 另在取正文时(需 DB,不在此)。
"""

from core.controller import resolve_skill_activation


def test_first_activation_injects_and_lists():
    to_inject, active = resolve_skill_activation(["a"], {"a": object()}, [])
    assert to_inject == ["a"]
    assert active == ["a"]


def test_invisible_slug_dropped():
    to_inject, active = resolve_skill_activation(["ghost"], {"a": object()}, [])
    assert to_inject == []
    assert active == []


def test_rearm_already_active_reinjects_but_list_deduped():
    # 重勾一个往轮已激活的 skill:正文重注入(在 to_inject),但名单不堆重复。
    to_inject, active = resolve_skill_activation(["a"], {"a": object()}, ["a"])
    assert to_inject == ["a"]      # 重注入
    assert active == ["a"]         # 名单去重(不是 ["a","a"])


def test_request_internal_dedup():
    to_inject, active = resolve_skill_activation(["a", "a"], {"a": object()}, [])
    assert to_inject == ["a"]      # 请求内去重
    assert active == ["a"]


def test_mix_new_and_rearmed_preserves_parent_then_new():
    # parent=[a];勾 [a(重), b(新), z(不可见)] → 注入 [a,b]、名单 [a,b]
    vis = {"a": object(), "b": object()}
    to_inject, active = resolve_skill_activation(["a", "b", "z"], vis, ["a"])
    assert to_inject == ["a", "b"]
    assert active == ["a", "b"]    # parent 的 a 在前,新的 b 追加,z 丢弃


def test_none_activate_skills():
    to_inject, active = resolve_skill_activation(None, {"a": object()}, ["a"])
    assert to_inject == []
    assert active == ["a"]         # 名单 = parent 原样


def test_parent_only_untouched_when_nothing_armed():
    to_inject, active = resolve_skill_activation([], {}, ["x", "y"])
    assert to_inject == []
    assert active == ["x", "y"]
