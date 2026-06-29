"""seeded 凭证 reconcile(env → 加密落库)+ snapshot 执行期解密 round-trip(B-4)。"""

import textwrap

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from config import config
from db.models import ToolCredential
from reconcile.reconciler import reconcile_config_to_db
from reconcile.snapshot import load_registry_snapshot
from tools.custom.credentials import CredentialCipher


def _tool_with_secret_md(name="ragflow", host_ph="TOOL_SECRET_RAGFLOW_HOST",
                         key_ph="TOOL_SECRET_RAGFLOW_KEY"):
    return (
        "---\n"
        f"name: {name}\n"
        'description: "RAGFlow query"\n'
        "type: http\n"
        "permission: auto\n"
        f'endpoint: "https://{{{{{host_ph}}}}}/api/query"\n'
        "method: GET\n"
        "headers:\n"
        f'  Authorization: "Bearer {{{{{key_ph}}}}}"\n'
        "parameters:\n"
        "  - name: q\n"
        "    type: string\n"
        '    description: "query"\n'
        "    required: true\n"
        "---\n"
        "Body.\n"
    )


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


@pytest.fixture
def cfg(tmp_path):
    tools = tmp_path / "tools"
    agents = tmp_path / "agents"
    tools.mkdir()
    agents.mkdir()
    return tools, agents


@pytest.fixture
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "CREDENTIAL_KEY", k)
    return k


async def _run(session, cfg):
    tools, agents = cfg
    return await reconcile_config_to_db(session, tools_dir=str(tools), agents_dir=str(agents))


async def _creds(session, unit):
    return {
        r.placeholder_name: r
        for r in (await session.execute(
            select(ToolCredential).where(ToolCredential.unit_name == unit)
        )).scalars().all()
    }


async def test_seeds_credentials_from_env_encrypted(db_session, cfg, key, monkeypatch):
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)

    rows = await _creds(db_session, "ragflow")
    assert set(rows) == {"TOOL_SECRET_RAGFLOW_HOST", "TOOL_SECRET_RAGFLOW_KEY"}
    cipher = CredentialCipher(key)
    assert rows["TOOL_SECRET_RAGFLOW_KEY"].encrypted_value != "k-123"      # 密文非明文
    assert cipher.decrypt(rows["TOOL_SECRET_RAGFLOW_KEY"].encrypted_value) == "k-123"
    assert all(r.source == "seeded" for r in rows.values())


async def test_idempotent_rerun_no_credential_change(db_session, cfg, key, monkeypatch):
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)
    before = {p: r.encrypted_value for p, r in (await _creds(db_session, "ragflow")).items()}

    await _run(db_session, cfg)
    after = {p: r.encrypted_value for p, r in (await _creds(db_session, "ragflow")).items()}
    # 同值 → 不重加密(Fernet 含随机 IV,重加密会变密文;不变证明走了 skip 路径)
    assert before == after


async def test_env_change_re_encrypts(db_session, cfg, key, monkeypatch):
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)

    # key 轮换:定义没变(unit hash skip)但 env 变 → 必须更新密文
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-ROTATED")
    await _run(db_session, cfg)

    rows = await _creds(db_session, "ragflow")
    assert CredentialCipher(key).decrypt(rows["TOOL_SECRET_RAGFLOW_KEY"].encrypted_value) == "k-ROTATED"


async def test_removed_placeholder_pruned(db_session, cfg, key, monkeypatch):
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)
    assert "TOOL_SECRET_RAGFLOW_KEY" in await _creds(db_session, "ragflow")

    # 定义去掉 header(不再引用 KEY 占位符)→ 该 seeded 凭证 prune
    no_header = _tool_with_secret_md().replace(
        '  Authorization: "Bearer {{TOOL_SECRET_RAGFLOW_KEY}}"\n', ""
    ).replace("headers:\n", "")
    _write(cfg[0] / "ragflow.md", no_header)
    await _run(db_session, cfg)

    rows = await _creds(db_session, "ragflow")
    assert set(rows) == {"TOOL_SECRET_RAGFLOW_HOST"}


async def test_missing_env_seeds_no_row(db_session, cfg, key, monkeypatch):
    # HOST 在 env、KEY 不在 → 只种 HOST,KEY 无行(工具调用时 loud-fail),不阻塞 reconcile
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.delenv("TOOL_SECRET_RAGFLOW_KEY", raising=False)
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    report = await _run(db_session, cfg)
    assert report  # reconcile 成功完成

    rows = await _creds(db_session, "ragflow")
    assert set(rows) == {"TOOL_SECRET_RAGFLOW_HOST"}


async def test_env_gone_keeps_existing_credential(db_session, cfg, key, monkeypatch):
    # env 先有值 → 种密文;随后 env 缺失但定义**仍引用**该占位符 → 保留旧密文(不删)。
    # reviewer #3:env-absent 是模糊信号(副本 .env 漏挂 / 注入先后),不在其上销毁共享状态。
    # 撤销只走"删 config 里的 {{...}} 引用"(见 test_removed_placeholder_pruned)。
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)
    assert "TOOL_SECRET_RAGFLOW_KEY" in await _creds(db_session, "ragflow")

    # KEY 从 env 消失,但定义没变(仍引用 {{TOOL_SECRET_RAGFLOW_KEY}})
    monkeypatch.delenv("TOOL_SECRET_RAGFLOW_KEY", raising=False)
    await _run(db_session, cfg)

    rows = await _creds(db_session, "ragflow")
    assert set(rows) == {"TOOL_SECRET_RAGFLOW_HOST", "TOOL_SECRET_RAGFLOW_KEY"}  # KEY 仍在
    assert CredentialCipher(key).decrypt(
        rows["TOOL_SECRET_RAGFLOW_KEY"].encrypted_value) == "k-123"


async def test_unit_prune_cascades_credentials(db_session, cfg, key, monkeypatch):
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-123")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)
    assert await _creds(db_session, "ragflow")

    (cfg[0] / "ragflow.md").unlink()  # config 删 unit → prune + 级联删凭证
    await _run(db_session, cfg)
    assert await _creds(db_session, "ragflow") == {}


async def test_snapshot_tool_decrypts_credential_at_execute(db_session, db_manager, cfg, key, monkeypatch):
    """端到端(B-5 lazy):reconcile 种密文 → snapshot 建 HttpTool + resolver(持 db_manager)
    → execute 期开短 session 解密替换。execute 前把 env 改成假值 —— 断言仍取到 DB 里的真值,
    即证明走的是 lazy DB 解密(短 session)而非 env 回落。"""
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "rag.local")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "k-secret")
    _write(cfg[0] / "ragflow.md", _tool_with_secret_md())
    await _run(db_session, cfg)
    await db_session.commit()  # 密文行落定:resolver 的 fresh with_retry session 才看得到

    # env 改成假值:execute 若误走 env 回落会得到 WRONG.*,故下面断言成立 = 确实走了 DB 解密
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_HOST", "WRONG.env")
    monkeypatch.setenv("TOOL_SECRET_RAGFLOW_KEY", "WRONG-key")

    snapshot = await load_registry_snapshot(db_session, db_manager=db_manager)
    tool = snapshot.external_tools["ragflow"]

    captured = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok":1}'
        def raise_for_status(self): pass
        def json(self): return {"ok": 1}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kwargs):
            captured.update(url=url, headers=kwargs.get("headers"))
            return _Resp()

    monkeypatch.setattr("tools.custom.http_tool.httpx.AsyncClient", _Client)
    result = await tool.execute(q="hello")
    assert result.success is True
    assert captured["url"] == "https://rag.local/api/query"
    assert captured["headers"]["Authorization"] == "Bearer k-secret"
