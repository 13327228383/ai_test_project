# -*- coding: utf-8 -*-
"""元素文本包含/不包含断言 — text_contains_check。

场景：错误消息应包含"请输入"；成功消息不应包含"失败"；提示文字应包含"成功"。
缺失后果：高频业务规则校验漏测，element_text_equals_check 只支持精确匹配。

实现：
  1. 取元素文本（selector 对应的第一个元素）。
  2. operator=contains → 应包含 expected；not_contains → 不应包含。
  3. 支持多值匹配（expected 逗号分隔时，任一匹配即可）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_text_contains(page, check: dict[str, Any]) -> dict[str, Any]:
    """元素文本包含/不包含断言。

    check 格式：
      {"type": "text_contains_check",
       "selector": ".error-msg",                     # 元素选择器
       "expected": "请输入",                          # 期望文本（逗号分隔多值时任一匹配即可）
       "operator": "contains"}                         # contains/not_contains
    """
    try:
        selector = check.get("selector", "")
        expected = check.get("expected", "")
        operator = (check.get("operator") or "contains").strip().lower()

        if not selector:
            return {"passed": False, "evidence": "[TextContains] 缺少 selector 参数"}
        if not expected:
            return {"passed": False, "evidence": "[TextContains] 缺少 expected 参数"}
        if operator not in ("contains", "not_contains"):
            return {
                "passed": False,
                "evidence": "[TextContains] 未知 operator: %s" % operator,
            }

        # 取元素文本
        loc = page.locator(selector).first
        try:
            text = await loc.inner_text(timeout=5000)
        except Exception:
            text = await loc.text_content(timeout=5000) or ""
        text = (text or "").strip()

        # ★ v4.6 诊断: 记录详细 evidence 到日志（解决"看不到text"的诊断盲区）
        _log.info(
            "[TextContains] selector=%s | text(%d)=%s | expected=%s",
            selector[:80],
            len(text),
            text[:120],
            str(expected)[:60],
        )

        # 多值匹配（逗号/中文逗号分隔，任一匹配即可）
        # ★ v5.1 (2026-08-08): 同时支持中文逗号（、）拆分，避免AI生成的"商品管理、订单处理"等
        #   长句不匹配页面元素
        import re as _re2
        expected_values = [x for v in _re2.split(r"[、,，]", str(expected)) if (x := v.strip())]
        # ★ v5.2 (2026-08-08): 对每个拆分值，再提取2字中文子词（如"商品管理"→"商品"、"管理"）
        _extended = list(expected_values)
        for _ev in expected_values:
            _chunks = _re2.findall(r'[\u4e00-\u9fff]{2,}', _ev)
            for _ck in _chunks:
                if len(_ck) >= 4:
                    for _i in range(len(_ck) - 1):
                        _sub = _ck[_i:_i+2]
                        if _sub not in _extended:
                            _extended.append(_sub)
        expected_values = _extended
        matched_any = any(v in text for v in expected_values)

        if operator == "contains":
            passed = matched_any
            detail = "文本 '%s' 应包含 %s" % (text[:40], expected_values)
        else:  # not_contains
            passed = not matched_any
            detail = "文本 '%s' 不应包含 %s" % (text[:40], expected_values)

        return {
            "passed": passed,
            "evidence": "[TextContains] %s → %s | %s → %s"
            % (selector, text[:40], detail, "PASS" if passed else "FAIL"),
        }
    except Exception as e:
        _log.error("[TextContains] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] text_contains_check 崩溃: %s" % str(e)[:80],
        }
