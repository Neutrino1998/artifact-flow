"""Tests for utils.password_policy.validate_password_strength (门类三 ACC-02 + 强度①)。

默认策略（config 默认值）:≥8 位、须含 字母+数字+符号 三类全、+ 弱口令/键盘/序列黑名单。
"""

import pytest

from config import config
from utils.password_policy import validate_password_strength


class TestValidPasswords:
    @pytest.mark.parametrize("pw", [
        "Abcd123!",        # 8 位,三类全
        "Str0ng#Pass",     # 较长
        "p@ssW0rd!2024",   # 含 password 子串但非纯弱口令且三类全
        "Zx9$mKq7",        # 随机风
    ])
    def test_strong_passwords_pass(self, pw):
        # 不抛即通过
        validate_password_strength(pw)


class TestLength:
    def test_too_short_rejected(self):
        with pytest.raises(ValueError, match="长度"):
            validate_password_strength("Ab1!")  # 4 位 < 8

    def test_exactly_min_length_ok(self):
        assert config.PASSWORD_MIN_LENGTH == 8
        validate_password_strength("Abcd123!")  # 恰 8 位


class TestComplexity:
    def test_missing_symbol_rejected(self):
        with pytest.raises(ValueError, match="符号"):
            validate_password_strength("Abcd1234")  # 字母+数字,无符号

    def test_missing_digit_rejected(self):
        with pytest.raises(ValueError, match="数字"):
            validate_password_strength("Abcdefg!")  # 字母+符号,无数字

    def test_missing_letter_rejected(self):
        with pytest.raises(ValueError, match="字母"):
            validate_password_strength("1234567!")  # 数字+符号,无字母

    def test_pure_digits_two_high_one_weak_example(self):
        # 两高一弱(一): 12345678
        with pytest.raises(ValueError):
            validate_password_strength("12345678")


class TestWeakPatterns:
    def test_single_char_repeat_rejected(self):
        # 即便理论上凑齐复杂度也先被长度/复杂度挡;这里测纯重复路径(放宽复杂度)
        config_symbol = config.PASSWORD_REQUIRE_SYMBOL
        config.PASSWORD_REQUIRE_SYMBOL = False
        config.PASSWORD_REQUIRE_DIGIT = False
        config.PASSWORD_REQUIRE_LETTER = False
        try:
            with pytest.raises(ValueError, match="重复"):
                validate_password_strength("aaaaaaaa")
        finally:
            config.PASSWORD_REQUIRE_SYMBOL = config_symbol
            config.PASSWORD_REQUIRE_DIGIT = True
            config.PASSWORD_REQUIRE_LETTER = True

    def test_sequential_run_rejected(self):
        orig_sym = config.PASSWORD_REQUIRE_SYMBOL
        orig_dig = config.PASSWORD_REQUIRE_DIGIT
        config.PASSWORD_REQUIRE_SYMBOL = False
        config.PASSWORD_REQUIRE_DIGIT = False  # "abcdefgh" 纯字母,放行复杂度才能测到序列检测
        try:
            with pytest.raises(ValueError, match="连续"):
                validate_password_strength("abcdefgh")  # 严格连续序列
        finally:
            config.PASSWORD_REQUIRE_SYMBOL = orig_sym
            config.PASSWORD_REQUIRE_DIGIT = orig_dig

    def test_keyboard_walk_with_full_complexity_rejected(self):
        # 两高一弱(四): 1qaz2wsx —— 这里凑齐三类仍因键盘序列被拒
        with pytest.raises(ValueError, match="键盘"):
            validate_password_strength("1Qaz2Wsx!")  # 含 "1qaz2wsx" 子串

    def test_weak_blacklist_when_symbol_relaxed(self):
        # 放宽 symbol 要求后,纯弱口令字典词应被黑名单拦下(默认策略下会先因缺符号被挡)
        orig = config.PASSWORD_REQUIRE_SYMBOL
        config.PASSWORD_REQUIRE_SYMBOL = False
        try:
            with pytest.raises(ValueError, match="弱口令"):
                validate_password_strength("password123")
        finally:
            config.PASSWORD_REQUIRE_SYMBOL = orig
