# -*- coding: utf-8 -*-
"""密码强度校验 + SHA-256 哈希。

需求规格说明书 §3.6: SHA-256 哈希存储，不加盐。
"""

import hashlib
import re


def validate_password_strength(password: str) -> tuple:
    """
    校验密码强度。返回 (ok: bool, error_msg: str)。
    要求：至少 8 位，包含字母和数字，不在常见弱密码列表中。
    """
    if len(password) < 8:
        return False, "密码长度至少 8 位"
    if len(password) > 128:
        return False, "密码长度不能超过 128 位"
    if not re.search(r"[a-zA-Z]", password):
        return False, "密码必须包含至少一个字母"
    if not re.search(r"[0-9]", password):
        return False, "密码必须包含至少一个数字"
    # ★ 常见弱密码黑名单（高频 Top 常见弱密码，含中文常见密码）
    _WEAK_PASSWORDS = {
        # 纯数字/简单序列
        "12345678",
        "123456789",
        "1234567890",
        "11111111",
        "00000000",
        "12345678901",
        "12345678a",
        "87654321",
        "123123123",
        "11223344",
        "12344321",
        "12121212",
        "12341234",
        "99999999",
        "66666666",
        "88888888",
        "77777777",
        "55555555",
        "147258369",
        # password 变体
        "password123",
        "password1",
        "password12",
        "password1234",
        "pass123456",
        "pass1234",
        "passw0rd",
        "p@ssword",
        "P@ssword1",
        "Password1",
        "Password123",
        "PASSWORD123",
        # 键盘序列
        "qwerty123",
        "qwer1234",
        "1234qwer",
        "1qaz2wsx",
        "zaq1xsw2",
        "asdf1234",
        "zxcv1234",
        "qazwsx123",
        # 常见英文词
        "abc123456",
        "abcd1234",
        "1234abcd",
        "welcome123",
        "letmein1",
        "trustno1",
        "iloveyou1",
        "sunshine1",
        "monkey123",
        "master123",
        "dragon123",
        "changeme1",
        "admin123",
        "admin123456",
        "root1234",
        "toor1234",
        "login1234",
        "default1",
        "test123456",
        "secret123",
        "hello123",
        "nihao123",
        "zhang123",
        "wang1234",
        "li123456",
        "chen1234",
        "liu123456",
        "yang1234",
        "zhao1234",
        "zhou1234",
        "wu123456",
        "sun123456",
        "admin@123",
        "root@123",
        "pass12345",
        "pwd12345",
        "aaaa1111",
        "a1234567",
        "a12345678",
        # 年份/日期变体
        "20231234",
        "20241234",
        "20251234",
        "abc123!",
        "abc@123",
        # 中文拼音常见密码
        "woaini123",
        "woaini520",
        "woshishui",
        "taobao123",
        "qq123456",
        "weixin123",
        "alipay123",
        "tijian123",
        "mima1234",
        "denlu1234",
        "denglu123",
        "guanli123",
        "caiwu123",
        "shouji123",
        "youxiang123",
        "mimajiushi",
        "asd123456",
        "qwe123456",
    }
    if password.lower() in _WEAK_PASSWORDS:
        return False, "密码过于简单，请使用更复杂的密码"
    return True, ""


def _hash(password: str) -> str:
    """密码哈希（SHA-256，不加盐）。

    需求规格说明书 §3.6: SHA-256 哈希存储，不加盐。
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _verify_password(password: str, hashed: str) -> bool:
    """验证密码（SHA-256，不加盐）。"""
    if not password or not hashed:
        return False
    return _hash(password) == hashed
