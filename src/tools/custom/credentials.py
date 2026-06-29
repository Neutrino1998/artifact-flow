"""
工具凭证加密 + 快照期解析(B-4)。

两块:
  - `CredentialCipher` —— Fernet 对称可逆加密(AES-128-CBC + HMAC)。主密钥单把、
    不轮转(config.CREDENTIAL_KEY)。**可逆、非哈希**:execute 时要解开把真 key 外发。
  - `resolve_all_credentials` —— **快照读边界**一次性把全部凭证行解密成
    {unit_name: {placeholder: 明文}}。引擎循环因此不再持 DB 句柄(执行生命周期 #4):
    snapshot 在唯一的读 DB 阶段解密,结果以纯 dict 灌进 HttpTool,execute 退回纯 CPU。
    单行解密失败(主密钥轮换 / 密文损坏)→ skip + WARNING,**不 raise** —— 一行坏数据
    不该炸掉整轮装配(snapshot 每 turn 每用户跑,爆炸半径须有界,同撞名 skip+log);
    受影响工具在 execute 期因占位符缺失 loud-fail(generic error)。主密钥缺/格式非法
    在 startup 由 validate_config 拦下,故此处无「key 有效性」分支。

红线(贯穿 B-4):此通路只发 operator/unit 级凭证给**受信 backend** HttpTool;
沙盒工具永不拿凭证、per-user 身份透传是另一根 defer 的轴(见 plan Non-goals)。
解密值只在 execute 期存在,永不进日志 / 事件 / definition / 给模型看的 catalog。
"""

from typing import Dict

from config import config
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class CredentialKeyError(RuntimeError):
    """主密钥缺失或格式非法(Fernet 要求 32B urlsafe-base64)。"""


class CredentialCipher:
    """Fernet 封装。构造即校验 key 格式(非法直接抛 CredentialKeyError)。"""

    def __init__(self, key: str):
        # 延迟 import:无凭证部署不必装 cryptography 也能 import 本模块的其它符号
        from cryptography.fernet import Fernet

        if not key:
            raise CredentialKeyError(
                "ARTIFACTFLOW_CREDENTIAL_KEY is not set; cannot encrypt/decrypt tool "
                "credentials. Generate one with: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except Exception as e:
            raise CredentialKeyError(
                "ARTIFACTFLOW_CREDENTIAL_KEY is not a valid Fernet key "
                "(need 32 url-safe base64-encoded bytes)"
            ) from e

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")


def get_cipher() -> CredentialCipher:
    """用 config.CREDENTIAL_KEY 造 cipher。缺失/非法 → CredentialKeyError(loud)。"""
    return CredentialCipher(config.CREDENTIAL_KEY)


async def resolve_all_credentials(repo, cipher_factory=get_cipher) -> Dict[str, Dict[str, str]]:
    """全部凭证行 → {unit_name: {placeholder: 明文}}。快照读边界一次性解密。

    无行 → {}(且不构造 cipher)。单行解密失败 → skip + WARNING,不 raise:坏一行只让
    该 unit 的工具在 execute 期因占位符缺失 loud-fail,不拖垮整轮装配。主密钥缺/格式非法
    已在 startup(validate_config)拦下,故此处不判 key 有效性。
    """
    rows = await repo.list_all()
    if not rows:
        return {}
    cipher = cipher_factory()
    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        try:
            plaintext = cipher.decrypt(r.encrypted_value)
        except Exception:
            # 主密钥轮换 / 密文损坏:非显然、ops 相关 → WARNING(loud)。skip 而非 raise,
            # 不让一行坏数据炸掉每 turn 每用户都跑的 snapshot 装配(爆炸半径有界)。
            logger.warning(
                "snapshot: failed to decrypt credential %s/%s (master key rotation or "
                "corrupted ciphertext) — tool will fail at call",
                r.unit_name, r.placeholder_name,
            )
            continue
        out.setdefault(r.unit_name, {})[r.placeholder_name] = plaintext
    return out
