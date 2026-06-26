"""
工具凭证加密 + 运行期解析(B-4)。

两块:
  - `CredentialCipher` —— Fernet 对称可逆加密(AES-128-CBC + HMAC)。主密钥单把、
    不轮转(config.CREDENTIAL_KEY)。**可逆、非哈希**:execute 时要解开把真 key 外发。
  - `CredentialResolver` —— 运行期读侧:按 unit 名查 tool_credentials 行、解密成
    {placeholder: 明文} map。**lazy 到 execute、只解被调工具的 unit**;无凭证行的
    unit 返回 {} 且永不构造 cipher(无凭证部署无需设 CREDENTIAL_KEY)。

红线(贯穿 B-4):此通路只发 operator/unit 级凭证给**受信 backend** HttpTool;
沙盒工具永不拿凭证、per-user 身份透传是另一根 defer 的轴(见 plan Non-goals)。
解密值只在 execute 期存在,永不进日志 / 事件 / definition / 给模型看的 catalog。
"""

from typing import Dict, Optional

from config import config
from tools.custom.secrets import SecretResolutionError
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


class CredentialResolver:
    """运行期凭证解析:按 unit 名查行 + 解密。HttpTool 在 execute 期持有并调用。

    句柄带 live DB session(经 repo);引擎执行在有 DB 的 turn session 上下文里、
    工具串行执行 → 无并发 session 复用。无凭证行的 unit 走快路径(返回 {}、不构造 cipher)。
    """

    def __init__(self, repo, cipher_factory=get_cipher):
        self._repo = repo
        self._cipher_factory = cipher_factory

    async def resolve(self, unit_name: Optional[str]) -> Dict[str, str]:
        """{placeholder: 明文}。无 unit / 无凭证行 → {}。解密失败 → SecretResolutionError。"""
        if not unit_name:
            return {}
        rows = await self._repo.list_for_unit(unit_name)
        if not rows:
            return {}
        try:
            cipher = self._cipher_factory()
        except CredentialKeyError as e:
            # 有密文行却没主密钥 = 部署配置错。execute 期 loud-fail(ops log),
            # HttpTool 捕成 generic 错误给模型,不外发占位符。
            raise SecretResolutionError(str(e)) from e
        out: Dict[str, str] = {}
        for r in rows:
            try:
                out[r.placeholder_name] = cipher.decrypt(r.encrypted_value)
            except SecretResolutionError:
                raise
            except Exception as e:
                raise SecretResolutionError(
                    f"failed to decrypt credential '{r.placeholder_name}' for unit "
                    f"'{unit_name}' (master key mismatch or corrupted ciphertext)"
                ) from e
        return out
