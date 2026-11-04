# -*- coding: utf-8 -*-
"""元素状态断言 — element_enabled_check / element_checked_check / select_value_check / element_text_equals_check。

验证元素的交互状态：启用/禁用、勾选/未勾选、下拉框选中值、文本精确匹配。
用于表单校验、按钮禁用、复选框状态等场景。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_state(page, check: dict[str, Any]) -> dict[str, Any]:
    """元素状态断言分派入口。

    check 格式：
      element_enabled_check:  {"type": "element_enabled_check", "selector": "...", "expected": true|false}
      element_checked_check:  {"type": "element_checked_check", "selector": "...", "expected": true|false}
      select_value_check:     {"type": "select_value_check", "selector": "...", "expected": "value"}
      element_text_equals_check: {"type": "element_text_equals_check", "selector": "...", "expected": "text"}
    """
    check_type = check.get("type", "")
    selector = check.get("selector", "")
    if not selector:
        return {"passed": True, "evidence": "无 selector，跳过"}

    if page is None:
        return {"passed": True, "evidence": "无 page，状态断言跳过"}

    try:
        if check_type == "element_enabled_check":
            return await _check_enabled(page, check, selector)
        elif check_type == "element_checked_check":
            return await _check_checked(page, check, selector)
        elif check_type == "select_value_check":
            return await _check_select_value(page, check, selector)
        elif check_type == "element_text_equals_check":
            return await _check_text_equals(page, check, selector)
        else:
            return {
                "passed": True,
                "evidence": "未知状态断言类型(%s)，跳过" % check_type,
            }
    except Exception as e:
        _log.warning("[StateCheck] 异常: %s", str(e)[:120])
        # ★ 根因修复(2026-09-21): 状态断言 fail-closed——异常不能冒充通过（假绿）
        return {"passed": False, "evidence": "状态断言异常: %s" % str(e)[:80]}


async def _check_enabled(page, check, selector) -> dict[str, Any]:
    """检查元素启用/禁用状态。"""
    expected_enabled = check.get("expected", True)
    try:
        locator = page.locator(selector).first
        if await locator.count() == 0:
            return {

                # ★ 根因修复(2026-09-21): 目标不存在时状态断言无法成立，fail-closed（防假绿/判伪误判）

                "passed": False,

                "evidence": "状态[\2] selector 未找到: %s" % selector,

            }
        is_disabled = await locator.is_disabled()
        actual_enabled = not is_disabled
        passed = actual_enabled == expected_enabled
        return {
            "passed": passed,
            "evidence": "状态[enabled] %s → %s (期望%s)"
            % (
                selector,
                "启用" if actual_enabled else "禁用",
                "启用" if expected_enabled else "禁用",
            ),
        }
    except Exception as e:
        # ★ 根因修复(2026-09-21): 异常 fail-closed（防假绿）
        

        return {"passed": False, "evidence": "状态[\2] 异常: %s" % str(e)[:60]}


async def _check_checked(page, check, selector) -> dict[str, Any]:
    """检查复选框/单选框勾选状态。"""
    expected_checked = check.get("expected", True)
    try:
        locator = page.locator(selector).first
        if await locator.count() == 0:
            return {

                # ★ 根因修复(2026-09-21): 目标不存在时状态断言无法成立，fail-closed（防假绿/判伪误判）

                "passed": False,

                "evidence": "状态[\2] selector 未找到: %s" % selector,

            }
        actual_checked = await locator.is_checked()
        passed = actual_checked == expected_checked
        return {
            "passed": passed,
            "evidence": "状态[checked] %s → %s (期望%s)"
            % (
                selector,
                "已勾选" if actual_checked else "未勾选",
                "已勾选" if expected_checked else "未勾选",
            ),
        }
    except Exception as e:
        # ★ 根因修复(2026-09-21): 异常 fail-closed（防假绿）
        

        return {"passed": False, "evidence": "状态[\2] 异常: %s" % str(e)[:60]}


async def _check_select_value(page, check, selector) -> dict[str, Any]:
    """检查下拉框选中值。"""
    expected_value = check.get("expected", "")
    try:
        locator = page.locator(selector).first
        if await locator.count() == 0:
            return {

                # ★ 根因修复(2026-09-21): 目标不存在时状态断言无法成立，fail-closed（防假绿/判伪误判）

                "passed": False,

                "evidence": "状态[\2] selector 未找到: %s" % selector,

            }
        actual_value = await locator.evaluate("el => el.value")
        passed = actual_value == expected_value
        return {
            "passed": passed,
            "evidence": "状态[select_value] %s → '%s' (期望'%s')"
            % (
                selector,
                actual_value,
                expected_value,
            ),
        }
    except Exception as e:
        # ★ 根因修复(2026-09-21): 异常 fail-closed（防假绿）
        

        return {"passed": False, "evidence": "状态[\2] 异常: %s" % str(e)[:60]}


async def _check_text_equals(page, check, selector) -> dict[str, Any]:
    """检查元素文本精确等于。"""
    expected_text = check.get("expected", "").strip()
    try:
        locator = page.locator(selector).first
        if await locator.count() == 0:
            # ★ 根因修复(2026-09-21): 目标不存在时状态断言无法成立，fail-closed（防假绿/判伪误判）
            return {
                "passed": False,
                "evidence": "状态[text_equals] selector 未找到: %s" % selector,
            }
        actual_text = (await locator.inner_text()).strip()
        passed = actual_text == expected_text
        return {
            "passed": passed,
            "evidence": "状态[text_equals] '%s' == '%s'"
            % (actual_text[:50], expected_text[:50]),
        }
    except Exception as e:
        # ★ 根因修复(2026-09-21): 异常 fail-closed（防假绿）
        return {"passed": False, "evidence": "状态[text_equals] 异常: %s" % str(e)[:60]}
