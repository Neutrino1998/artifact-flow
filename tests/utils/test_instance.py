"""utils.instance — instance_id 铸造规则"""

from utils import instance


def test_instance_id_is_nonempty_and_path_safe():
    """会用作日志子目录名与响应头值:非空、字符集收窄。"""
    assert instance.INSTANCE_ID
    assert all(c.isalnum() or c in "._-" for c in instance.INSTANCE_ID)
    assert len(instance.INSTANCE_ID) <= 64


def test_mint_env_override(monkeypatch):
    monkeypatch.setenv("ARTIFACTFLOW_INSTANCE_ID", "backend-1")
    assert instance._mint() == "backend-1"


def test_mint_sanitizes_unsafe_chars(monkeypatch):
    """env 覆盖手滑塞路径分隔符/空格/非 ASCII → 收窄为安全字符集。"""
    monkeypatch.setenv("ARTIFACTFLOW_INSTANCE_ID", "a/b\\c d:e中")
    minted = instance._mint()
    assert minted == "a-b-c-d-e-"
    assert "/" not in minted and "\\" not in minted


def test_mint_truncates_to_64(monkeypatch):
    monkeypatch.setenv("ARTIFACTFLOW_INSTANCE_ID", "x" * 200)
    assert len(instance._mint()) == 64


def test_mint_rejects_path_semantic_values(monkeypatch):
    """'.' 会使分目录静默塌回平铺、'..' 逃逸到上级 —— 字符集守卫拦不住,单独拒。"""
    for bad in (".", ".."):
        monkeypatch.setenv("ARTIFACTFLOW_INSTANCE_ID", bad)
        assert instance._mint() == "unknown", bad
    # '/' 属字符集守卫的辖区:替换成 '-' 即无路径语义,不必拒
    monkeypatch.setenv("ARTIFACTFLOW_INSTANCE_ID", "/")
    assert instance._mint() == "-"
