# -*- coding: utf-8 -*-
# 注：模块 docstring 用原始字符串（r"""），内含 \d / \w 等正则转义，
# 普通字符串会触发 SyntaxWarning: invalid escape sequence（Python 3.12+ 为 SyntaxError 预警）。
r"""文本格式正则断言 — text_regex_check。

场景：手机号 ^1[3-9]\d{9}$、邮箱 ^\w+@\w+\.com$、身份证号格式校验。
缺失后果：业务格式规则漏测，只能精确匹配（element_text_equals_check 无法覆盖格式族）。

实现：
  1. 取元素文本（selector 对应的第一个元素）。
  2. 用 re.fullmatch 匹配 pattern（要求整段文本匹配）。
  3. expected=true → 应匹配；expected=false → 不应匹配。
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


async def verify_text_regex(page, check: dict[str, Any]) -> dict[str, Any]:
    """文本格式正则断言。

    check 格式：
      {"type": "text_regex_check",
       "selector": ".phone-display",                 # 元素选择器
       "pattern": "^1[3-9]\\d{9}$",                  # 正则表达式（fullmatch 整段匹配）
       "expected": true}                              # true=应匹配 / false=不应匹配
    """
    try:
        selector = check.get("selector", "")
        pattern = check.get("pattern", "")
        expected = check.get("expected", True)
        if isinstance(expected, str):
            expected = expected.lower() not in ("false", "0", "no", "")

        if not selector:
            return {"passed": False, "evidence": "[RegexCheck] 缺少 selector 参数"}
        if not pattern:
            return {"passed": False, "evidence": "[RegexCheck] 缺少 pattern 参数"}

        # 编译正则（捕获非法表达式）
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {
                "passed": False,
                "evidence": "[RegexCheck] 非法正则 '%s': %s" % (pattern, str(e)[:60]),
            }

        # 取元素文本
        loc = page.locator(selector).first
        try:
            text = await loc.inner_text(timeout=5000)
        except Exception:
            text = await loc.text_content(timeout=5000) or ""
        text = (text or "").strip()

        # fullmatch 要求整段文本匹配（避免部分匹配误判）
        matched = bool(regex.fullmatch(text))

        passed = matched if expected else (not matched)

        return {
            "passed": passed,
            "evidence": "[RegexCheck] %s text='%s' pattern='%s' matched=%s expected=%s → %s"
            % (
                selector,
                text[:40],
                pattern,
                matched,
                expected,
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[RegexCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] text_regex_check 崩溃: %s" % str(e)[:80],
        }
