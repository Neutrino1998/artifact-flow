"""
工具凭证加密 + 运行期解析(B-4;B-5 退回 lazy decrypt-at-execute)。

两块:
  - `CredentialCipher` —— Fernet 对称可逆加密(AES-128-CBC + HMAC)。主密钥单把、
    不轮转(config.CREDENTIAL_KEY)。**可逆、非哈希**:execute 时要解开把真 key 外发。
  - `CredentialResolver` —— 运行期读侧:execute 期按 unit 名查 tool_credentials 行、
    解密成 {placeholder: 明文} map。**lazy 到 execute、只解被调工具的 unit**。

    与 turn-long session 解耦(B-5):resolver 持 `db_manager`(**不是**某条 live
    session),每次 resolve 经 `with_retry` 开一条**短的、带重试的** session,读完即关。
    旧设计骑 turn-long session、execute 期 await DB —— 那是 idle-in-transaction +
    turn 内唯一无 fresh-session 重试的 DB 读(执行生命周期 #4),B-5 一并消掉。

    **零明文缓存**:解密值只作单次 resolve 的返回局部存活,既不挂 resolver、也不挂
    HttpTool 实例 → 不驻留整轮(消 N1)。代价:同一 unit 被多次调用各自重读重解(已
    权衡接受 —— 合规姿态优先于省一次查询)。cipher 每 resolver 只造一次(避 per-call
    重建,#11);无凭证行的 unit 返回 {} 且不构造 cipher(无凭证部署无需设主密钥)。

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
    """运行期凭证解析:execute 期按 unit 名查行 + 解密。HttpTool 持有并在 execute 调用。

    持 `db_manager`(非某条 live session)→ 每次 resolve 用 `with_retry` 开短 session
    读密文、读完即关(B-5:不骑 turn-long session,补瞬断重试)。cipher 每 resolver 只
    造一次(避 per-call 重建,#11);**解密明文绝不缓存** —— 只作返回值,用完即弃(消 N1
    明文驻留整轮)。无 unit / 无凭证行 → {}、且不构造 cipher。
    """

    def __init__(self, db_manager, cipher_factory=get_cipher):
        self._db_manager = db_manager
        self._cipher_factory = cipher_factory
        self._cipher = None  # lazy:首个有凭证行的 unit 解析时构造一次,之后复用

    async def resolve(self, unit_name: Optional[str]) -> Dict[str, str]:
        """{placeholder: 明文}。无 unit / 无凭证行 → {}。解密失败 → SecretResolutionError。

        读 + 解密全程在 `with_retry` 的短 session 回调内完成 —— ORM 行不外逃(只在
        session 内读其密文列),返回纯 dict。单行解密失败 **raise**(非 skip):lazy 路径
        下被解的就是被调工具的 unit,缺凭证会让 execute 把 {{NAME}} 原文外发,故宁可
        loud-fail 成 generic 错误(爆炸半径 = 仅该次调用,与 snapshot 期 eager 不同)。
        """
        if not unit_name:
            return {}

        from repositories.tool_credential_repo import ToolCredentialRepository

        async def _read_and_decrypt(session) -> Dict[str, str]:
            rows = await ToolCredentialRepository(session).list_for_unit(unit_name)
            if not rows:
                return {}
            if self._cipher is None:
                self._cipher = self._cipher_factory()
            out: Dict[str, str] = {}
            for r in rows:
                try:
                    out[r.placeholder_name] = self._cipher.decrypt(r.encrypted_value)
                except Exception as e:
                    raise SecretResolutionError(
                        f"failed to decrypt credential '{r.placeholder_name}' for unit "
                        f"'{unit_name}' (master key mismatch or corrupted ciphertext)"
                    ) from e
            return out

        return await self._db_manager.with_retry(_read_and_decrypt)
