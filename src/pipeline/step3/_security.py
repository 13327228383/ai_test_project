# -*- coding: utf-8 -*-
"""安全/脱敏工具（从 step3.py 拆分）"""

import re


def _sanitize_credentials(text: str) -> str:
    """
    ★ 安全修复：脱敏敏感凭据，防止明文发送到外部 AI API。
    - 密码字段：保留前2位，其余用 **** 替代
    - API Key：保留前4位，其余用 **** 替代
    - URL中的用户名:密码：脱敏密码部分
    """
    if not text:
        return text
    # 脱敏 password=xxx / 密码：xxx
    # 支持中文全角冒号（：U+FF1A）和英文半角冒号（:U+003A）
    result = re.sub(r"(密码[\s:=\uff1a]+)\S+", r"\1****", text)
    # 脱敏 password:xxx / pwd=xxx
    result = re.sub(
        r"(password|pwd)[\s:=]+\S+", r"\1:****", result, flags=re.IGNORECASE
    )
    # 脱敏 API Key（16+位连续字符）
    result = re.sub(
        r"(api[_-]?key)[\s:=]+([A-Za-z0-9+/=_-]{16,})",
        r"\1****",
        result,
        flags=re.IGNORECASE,
    )
    # 脱敏 URL 中的密码 (http://user:password@host)
    result = re.sub(r"(https?://[^:]+:)\S+(@)", r"\1****\2", result)
    return result
