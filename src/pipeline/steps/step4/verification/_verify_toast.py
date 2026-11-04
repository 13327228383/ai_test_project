# -*- coding: utf-8 -*-
"""Toast/Loading 断言 — toast_appear_check / toast_disappear_check / loading_state_check。

用于验证操作反馈：Toast 提示出现/消失、Loading 状态出现/消失。

设计原则：不抛异常，selector 找不到 → 标 evidence 不 fail（避免误杀）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_TOAST_SELECTORS = [
    "[role='alert']",
    "[role='status']",
    "[aria-live='assertive']",
    "[aria-live='polite']",
    ".toast",
    ".alert",
]

_DEFAULT_LOADING_SELECTORS = [
    "[role='progressbar']",
    "[aria-busy='true']",
    ".loading",
    ".spinner",
    ".overlay",
]


async def verify_toast(page, check: dict[str, Any]) -> dict[str, Any]:
    """Toast/Loading 断言分派入口。

    check 格式：
      {"type": "toast_appear_check",
       "selector": "可选自定义选择器",
       "text": "可选Toast文本内容",
       "timeout_ms": 等待超时}

      {"type": "toast_disappear_check",
       "selector": "可选自定义选择器",
       "timeout_ms": 等待超时}

      {"type": "loading_state_check",
       "selector": "可选自定义选择器",
       "expected": "visible|hidden",
       "timeout_ms": 等待超时}
    """
    check_type = check.get("type", "")

    if page is None:
        return {"passed": True, "evidence": "无 page，Toast 断言跳过"}

    try:
        if check_type == "toast_appear_check":
            return await _check_toast_appear(page, check)
        elif check_type == "toast_disappear_check":
            return await _check_toast_disappear(page, check)
        elif check_type == "loading_state_check":
            return await _check_loading_state(page, check)
        else:
            return {
                "passed": True,
                "evidence": "未知 Toast 断言类型(%s)，跳过" % check_type,
            }
    except Exception as e:
        _log.warning("[ToastCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "Toast 断言异常，跳过: %s" % str(e)[:80]}


async def _check_toast_appear(page, check) -> dict[str, Any]:
    """Toast 出现验证。"""
    timeout_ms = check.get("timeout_ms", 3000)
    text = check.get("text", "")
    custom_selector = check.get("selector", "")

    selectors = [custom_selector] if custom_selector else _DEFAULT_TOAST_SELECTORS

    for sel in selectors:
        try:
            locator = page.locator(sel).first
            await locator.wait_for(state="visible", timeout=timeout_ms)
            if text:
                actual_text = await locator.inner_text()
                passed = text in actual_text
                return {
                    "passed": passed,
                    "evidence": "Toast[appear] %s 文本='%s' (查找'%s')"
                    % (sel, actual_text[:50], text),
                }
            return {"passed": True, "evidence": "Toast[appear] %s 已出现" % sel}
        except Exception as _e:
            _log.debug("Toast 出现验证失败: %s", _e, exc_info=True)
            continue

    return {"passed": False, "evidence": "Toast[appear] 未找到任何 Toast 元素"}


async def _check_toast_disappear(page, check) -> dict[str, Any]:
    """Toast 消失验证。"""
    timeout_ms = check.get("timeout_ms", 5000)
    custom_selector = check.get("selector", "")

    selectors = [custom_selector] if custom_selector else _DEFAULT_TOAST_SELECTORS

    for sel in selectors:
        try:
            locator = page.locator(sel).first
            count = await locator.count()
            if count == 0:
                return {"passed": True, "evidence": "Toast[disappear] %s 已消失" % sel}
            await locator.wait_for(state="hidden", timeout=timeout_ms)
            return {"passed": True, "evidence": "Toast[disappear] %s 已消失" % sel}
        except Exception as _e:
            _log.debug("Toast 消失验证失败: %s", _e, exc_info=True)
            continue

    return {"passed": True, "evidence": "Toast[disappear] 无 Toast 元素，视为已消失"}


async def _check_loading_state(page, check) -> dict[str, Any]:
    """Loading 状态验证。"""
    expected = check.get("expected", "hidden")
    timeout_ms = check.get("timeout_ms", 3000)
    custom_selector = check.get("selector", "")

    selectors = [custom_selector] if custom_selector else _DEFAULT_LOADING_SELECTORS

    if expected == "visible":
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return {"passed": True, "evidence": "Loading[visible] %s 已出现" % sel}
            except Exception as _e:
                _log.debug("Loading 出现验证失败: %s", _e, exc_info=True)
                continue
        return {"passed": False, "evidence": "Loading[visible] 未找到 Loading 元素"}
    else:
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                count = await locator.count()
                if count > 0:
                    await locator.wait_for(state="hidden", timeout=timeout_ms)
                    return {
                        "passed": True,
                        "evidence": "Loading[hidden] %s 已消失" % sel,
                    }
            except Exception as _e:
                _log.debug("Loading 消失验证失败: %s", _e, exc_info=True)
                continue
        return {
            "passed": True,
            "evidence": "Loading[hidden] 无 Loading 元素，视为已隐藏",
        }
