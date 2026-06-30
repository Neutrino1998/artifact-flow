"""EffectiveSkillSet 可见性 resolver 单测(C-2,决策 1/10)。

覆盖:private(owner)/ public(默认可见、dept 例外=deny)/ department(默认隐、dept 例外
=grant)三态;enabled = default_enabled + user_skill 覆盖;available_for_l1 过滤 + 顺序。
"""

from core.effective_skillset import resolve_effective_skillset
from reconcile.snapshot import SkillInfo


def _info(slug, visibility="public", default_enabled=True, owner=None):
    return SkillInfo(
        slug=slug, name=slug, description=f"desc-{slug}", visibility=visibility,
        default_enabled=default_enabled, owner_user_id=owner, allowed_tools=[],
    )


def _resolve(snapshot, user="u1", overrides=None, dept_matched=None):
    return resolve_effective_skillset(user, snapshot, overrides or {}, dept_matched or set())


def test_public_visible_by_default():
    eff = _resolve({"a": _info("a", "public")})
    assert "a" in eff.visible
    assert "a" in eff.enabled  # default_enabled=True


def test_public_with_dept_rule_is_denied():
    # public + dept 命中 = deny 例外 → 不可见
    eff = _resolve({"a": _info("a", "public")}, dept_matched={"a"})
    assert "a" not in eff.visible


def test_department_hidden_without_grant():
    eff = _resolve({"a": _info("a", "department")})
    assert "a" not in eff.visible


def test_department_visible_with_grant():
    eff = _resolve({"a": _info("a", "department")}, dept_matched={"a"})
    assert "a" in eff.visible


def test_private_visible_only_to_owner():
    snap = {"a": _info("a", "private", owner="u1"), "b": _info("b", "private", owner="u2")}
    eff = _resolve(snap, user="u1")
    assert "a" in eff.visible
    assert "b" not in eff.visible


def test_default_enabled_false_visible_but_not_in_l1():
    eff = _resolve({"a": _info("a", "public", default_enabled=False)})
    assert "a" in eff.visible
    assert "a" not in eff.enabled


def test_user_override_enables_default_off():
    eff = _resolve({"a": _info("a", "public", default_enabled=False)}, overrides={"a": True})
    assert "a" in eff.enabled


def test_user_override_disables_default_on():
    # 用户关掉默认开的 → 仍 visible(可 /skill opt-in),不进 L1
    eff = _resolve({"a": _info("a", "public", default_enabled=True)}, overrides={"a": False})
    assert "a" in eff.visible
    assert "a" not in eff.enabled


def test_available_for_l1_filters_and_orders():
    snap = {
        "a": _info("a", "public", default_enabled=True),
        "b": _info("b", "public", default_enabled=False),
        "c": _info("c", "public", default_enabled=True),
    }
    eff = _resolve(snap)
    l1 = [s.slug for s in eff.available_for_l1()]
    assert l1 == ["a", "c"]  # b default-off 排除,顺序保 snapshot
