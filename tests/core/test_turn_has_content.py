"""turn_has_content — 空输入不变量的单一真相谓词(#1 修复)。

关键:activate_skills 是意图,可能全被可见性/去重/空 body 过滤成空 —— 该闸只认**解析后**的
activated_skill_bodies,与上传/compact(presence ⟺ 注入非空)对齐,不认 raw 请求。
"""

from core.engine import turn_has_content


def test_plain_text_has_content():
    assert turn_has_content("hello") is True


def test_blank_text_no_augment_is_empty():
    assert turn_has_content("") is False
    assert turn_has_content("   \n\t") is False


def test_uploads_alone_have_content():
    assert turn_has_content("", uploaded_files=[{"filename": "a.pdf"}]) is True


def test_force_compact_alone_has_content():
    assert turn_has_content("", force_compact=True) is True


def test_resolved_skill_bodies_alone_have_content():
    assert turn_has_content("", activated_skill_bodies=[{"slug": "s", "body": "x"}]) is True


def test_empty_resolved_bodies_is_empty():
    # 这正是 #1:raw activate_skills 非空但解析后 bodies 空 → 判为无内容(该拒)。
    assert turn_has_content("", activated_skill_bodies=[]) is False
    assert turn_has_content("", activated_skill_bodies=None) is False


def test_text_plus_bodies_has_content():
    assert turn_has_content("do it", activated_skill_bodies=[{"slug": "s", "body": "x"}]) is True
