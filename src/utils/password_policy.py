"""密码强度策略（等保 9.1.4.1 身份鉴别 + 两高一弱弱口令规则）。

职责:在所有「设置/修改密码」入口复用同一套强度校验 —— schema validator、
CSV 批量导入、create_admin 脚本。失败抛 ValueError（带具体原因),由调用方
决定转成 422 / 400。

刻意不做（YAGNI / 见 docs/_archive/reviews/sec-review-findings.md「门类三」）:
- 历史口令不重用:那是 password_changed_at + password_history 列 + 改密端点的事,
  不在强度校验里(强度只看口令本身,不看账户状态)。
- 登录口令不在此校验:老用户带的旧密码可能不达标,登录只鉴别、不二次卡策略,
  也不泄露策略细节。仅 create/update/change 三类「写入新口令」的入口校验。

强度档由 config 常量驱动（operator/测试中心可调,不改码):
  PASSWORD_MIN_LENGTH / REQUIRE_LETTER / REQUIRE_DIGIT / REQUIRE_SYMBOL。

弱口令/键盘序列黑名单是 best-effort、非穷举 —— 覆盖两高一弱给的典型例子
（默认口令、纯数字/字母、键盘行走、连续/重复串),挡住绝大多数低质口令,
但不号称能识别一切弱口令(那是离线字典/zxcvbn 的活,过度工程,暂不上)。
"""

from __future__ import annotations

import re

from config import config


# 符号集:键盘可见的非字母数字 ASCII。等保「符号」即特殊字符,这里用
# 「既非字母也非数字的可打印字符」判定,涵盖 !@#$%^&*()-_=+[]{};:'",.<>/?\|`~ 等。
_SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"[0-9]")


# 弱口令黑名单（小写比对）。覆盖两高一弱(五)默认口令 + (一)(二)典型例子 +
# OWASP/常见泄露榜高频项。非穷举,best-effort。
WEAK_PASSWORDS: frozenset[str] = frozenset({
    # 两高一弱明确点名 / 设备默认口令
    "root", "password", "passwd", "admin", "administrator", "administ",
    "administor", "guest", "test", "user", "manager", "system", "oracle",
    "mysql", "default", "changeme", "letmein", "welcome", "login",
    # 纯数字 / 纯字母典型弱口令
    "111111", "000000", "123123", "112233", "11223344", "123321",
    "654321", "666666", "888888", "abcdef", "abcdefg", "abcabc",
    # 常见组合
    "password1", "password123", "admin123", "root123", "qwerty123",
    "iloveyou", "monkey", "dragon", "master", "superman",
})

# 键盘行走串（小写比对,子串命中即拒）。两高一弱(四)点名的 qweasdzxc / 1qaz2wsx
# 等「键盘相邻按键」组合,加常见横向/纵向行走。
_KEYBOARD_WALKS: tuple[str, ...] = (
    "qwertyuiop", "asdfghjkl", "zxcvbnm",
    "qweasdzxc", "qazwsxedc", "1qaz2wsx", "2wsx3edc", "1q2w3e4r",
    "qwerty", "asdfgh", "zxcvbn", "qweasd", "1qazxsw2",
    "!qaz@wsx",
)

# 最小长度的纯递增/递减/重复检测窗口 —— 用于两高一弱(一)1111 / 123456 这类。
# 单字符重复(aaaa / 1111)与连续序列(1234 / abcd / 4321 / dcba)。
_MIN_RUN_TO_REJECT = 4  # 整条口令若是「单一重复」或「严格连续序列」即判弱


def _is_single_char_repeat(s: str) -> bool:
    """整条口令是同一个字符重复（1111 / aaaa）。"""
    return len(s) >= _MIN_RUN_TO_REJECT and len(set(s)) == 1


def _is_sequential_run(s: str) -> bool:
    """整条口令是严格连续的 codepoint 序列（123456 / abcdef / fedcba / 654321）。

    仅当全串单调步进 ±1 时判真;混入任何非连续即放行（交给其他规则）。
    """
    if len(s) < _MIN_RUN_TO_REJECT:
        return False
    deltas = {ord(b) - ord(a) for a, b in zip(s, s[1:])}
    return deltas in ({1}, {-1})


def validate_password_strength(plain: str) -> None:
    """校验明文口令是否满足强度策略。

    Args:
        plain: 明文新口令。

    Raises:
        ValueError: 不达标,message 为具体、可直接展示给用户的中文原因。
    """
    if plain is None:
        raise ValueError("口令不能为空")

    if len(plain) < config.PASSWORD_MIN_LENGTH:
        raise ValueError(f"口令长度不足，至少需要 {config.PASSWORD_MIN_LENGTH} 位")

    missing: list[str] = []
    if config.PASSWORD_REQUIRE_LETTER and not _LETTER_RE.search(plain):
        missing.append("字母")
    if config.PASSWORD_REQUIRE_DIGIT and not _DIGIT_RE.search(plain):
        missing.append("数字")
    if config.PASSWORD_REQUIRE_SYMBOL and not _SYMBOL_RE.search(plain):
        missing.append("符号")
    if missing:
        raise ValueError(f"口令复杂度不足，必须同时包含{'、'.join(missing)}")

    lowered = plain.lower()

    if lowered in WEAK_PASSWORDS:
        raise ValueError("口令属于常见弱口令，请更换")

    if _is_single_char_repeat(plain):
        raise ValueError("口令不能是单一字符的重复")

    if _is_sequential_run(plain):
        raise ValueError("口令不能是连续的字符序列")

    for walk in _KEYBOARD_WALKS:
        if walk in lowered:
            raise ValueError("口令包含键盘相邻按键序列，易被猜测，请更换")
