# -*- coding: utf-8 -*-
"""URL 匹配增强：通配符 + 正则匹配 — 从 verification.py 拆出，零业务逻辑变更。"""

import logging
import re as _re

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# URL 匹配增强：支持通配符 * 和正则匹配
# ══════════════════════════════════════════════════════════
def _match_url_pattern(check_url: str, actual_url: str) -> bool:
    """
    支持三种匹配模式：
    1. 精确包含：check_url 在 actual_url 中
    2. 通配符：* 匹配任意字符序列
    3. 正则：以 ^ 开头表示正则表达式
    """
    if not check_url or not actual_url:
        return False
    check_lower = check_url.lower()
    actual_lower = actual_url.lower()

    # 正则模式
    if check_lower.startswith("^"):
        try:
            return bool(_re.search(check_url, actual_url, _re.IGNORECASE))
        except _re.error as _e:
            _log.debug("[url_match] 正则匹配异常: %s", _e, exc_info=True)

    # 通配符模式
    if "*" in check_lower:
        pattern = check_lower.replace("*", ".*")
        try:
            return bool(_re.search(pattern, actual_lower))
        except _re.error as _e:
            _log.debug("[url_match] 正则匹配异常: %s", _e, exc_info=True)

    # 默认：包含匹配
    return check_lower in actual_lower
