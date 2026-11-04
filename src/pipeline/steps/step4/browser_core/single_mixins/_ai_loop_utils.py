# -*- coding: utf-8 -*-
"""AI loop 模块级辅助函数 — 从 _ai_loop.py 拆分（§4.3 单文件≤800行）。

职责：操作指令库反哺、DOM 元素查找、op_cache 格式转换、可用元素提取。
被 _ai_loop.py 的 _run_ai_round_loop 调用（模块级函数，无 self 依赖）。
"""
import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


# ── 操作指令库自动丰富辅助函数 ──────────────────────────────────────


def _enrich_action_tags(action_id: str, element_info) -> None:
    """把 AI 兜底成功的操作反哺到操作指令库（不阻断主流程）。

    Args:
        action_id: 操作类型（如 "click", "fill"）
        element_info: dict（SmartLocator 的 best_el）或 str（tag 名称）
    """
    with guard("_enrich_action_tags: from src.pipeline.utils.action_enric", log=_log):
        from src.pipeline.utils.action_enrichment import enrich_action_applicable_tags

        if isinstance(element_info, dict):
            _tag = str(element_info.get("tag", "") or "").strip()
            _role = str(element_info.get("role", "") or "").strip()
        else:
            _tag = str(element_info or "").strip()
            _role = ""
        if _tag:
            enrich_action_applicable_tags(action_id, _tag, _role or None)


def _find_element_tag_by_selector(dom_elements: list, selector: str) -> str:
    """从 DOM 元素列表中按 selector 匹配，返回元素的 tag。"""
    if not dom_elements or not selector:
        return ""
    try:
        for el in dom_elements:
            if not isinstance(el, dict):
                continue
            el_selector = el.get("selector", "") or ""
            if el_selector and el_selector == selector:
                return str(el.get("tag", "") or "").strip()
        # 模糊匹配：selector 包含在元素 selector 中
        for el in dom_elements:
            if not isinstance(el, dict):
                continue
            el_selector = el.get("selector", "") or ""
            if el_selector and (selector in el_selector or el_selector in selector):
                return str(el.get("tag", "") or "").strip()
    except Exception as _e:
        _log.debug("_find_element_tag_by_selector 失败: %s", _e, exc_info=True)
    return ""


# ── op_cache 格式转换辅助函数 ──────────────────────────────────────


def _selector_to_target_hint(selector: str, dom_elements: list) -> str:
    """把 AI 输出的 selector 转为 op_cache 的 target_hint 格式。

    op_cache 的 _find_by_hint 只识别 text=/placeholder=/label=/#/. /name= 前缀。
    AI 返回的 selector 可能是自然语言、CSS 选择器或 ai_id= 前缀。
    通过在 dom_elements 中查找匹配元素，提取其 text/placeholder/label 生成 hint。
    """
    if not selector:
        return ""
    # 已经是 hint 格式 → 直接返回
    if selector.startswith(("text=", "placeholder=", "label=", "name=", "#", ".")):
        return selector
    # ai_id= 是页面级临时 ID，无法跨页面复用 → 不缓存
    if selector.startswith("ai_id="):
        return ""
    # 自然语言或 CSS → 在 DOM 中找匹配元素
    for el in dom_elements or []:
        if not isinstance(el, dict):
            continue
        el_selector = el.get("selector", "")
        if el_selector and el_selector == selector:
            el_text = el.get("text", "")
            el_ph = el.get("placeholder", "")
            el_label = el.get("aria-label", "")
            if el_text:
                return "text=%s" % el_text
            if el_ph:
                return "placeholder=%s" % el_ph
            if el_label:
                return "label=%s" % el_label
            return ""
    # 模糊匹配：selector 包含在元素 text/placeholder/label 中
    _sel_lower = selector.lower()
    for el in dom_elements or []:
        if not isinstance(el, dict):
            continue
        el_text = (el.get("text", "") or "").lower()
        el_ph = (el.get("placeholder", "") or "").lower()
        el_label = (el.get("aria-label", "") or "").lower()
        if el_text and _sel_lower in el_text:
            return "text=%s" % el.get("text", "")
        if el_ph and _sel_lower in el_ph:
            return "placeholder=%s" % el.get("placeholder", "")
        if el_label and _sel_lower in el_label:
            return "label=%s" % el.get("aria-label", "")
    return ""


def _decide_to_cache_action(ai_data: dict, dom_elements: list) -> dict:
    """把 decide_action 返回的 {action_type, action_params} 转为 op_cache 的 {action, target_hint, ...} 格式。

    返回 None 表示不可缓存（fail/_guard_reject/ai_id 选择器）。
    """
    if not isinstance(ai_data, dict):
        return None
    action_type = ai_data.get("action_type", "")
    action_params = ai_data.get("action_params", {}) or {}

    if action_type in ("fail", "_guard_reject", ""):
        return None

    cached = {"action": action_type}

    if action_type in ("click", "fill"):
        selector = action_params.get("selector", "")
        hint = _selector_to_target_hint(selector, dom_elements)
        if hint:
            cached["target_hint"] = hint
        else:
            return None  # 无法生成可复用的 target_hint → 不缓存
        if action_type == "fill":
            cached["text"] = action_params.get("value", "")

    elif action_type == "navigate":
        url = action_params.get("url", "")
        if url:
            cached["target_hint"] = url
        else:
            return None

    elif action_type == "assert":
        # assert 不缓存：assert_type 丢失会导致回放时静默通过，且 ai_id= 格式与
        # Playwright locator() 不兼容（assert 执行器不像 click/fill 那样转换 ai_id= 前缀）
        return None

    elif action_type == "done":
        cached["result"] = "done"

    else:
        return None  # 未知 action_type → 不缓存

    return cached


def _extract_available_elements(dom_data) -> str:
    """从 DOM 数据中提取可用交互元素列表。

    ★ 通用修复 (2026-07-18): 用于失败报告，帮助开发者理解为什么元素不存在。
    注意：这是诊断信息，不是让AI找替代元素。

    支持两种格式:
    1. list[dict]: PlaywrightEngine.get_structured_state() 返回的格式
    2. str: page_dom_snapshots 的文本格式
    """
    import re

    _available = []
    _seen = set()

    if isinstance(dom_data, list):
        # 格式1: list[dict] — PlaywrightEngine 结构化状态
        for el in dom_data:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag", "") or ""
            role = el.get("role", "") or ""
            text = el.get("text", "") or ""
            placeholder = el.get("placeholder", "") or ""
            aria_label = el.get("ariaLabel", "") or ""

            # 只关注交互元素
            if (
                tag not in ("button", "input", "a", "select", "textarea")
                and role != "button"
            ):
                continue

            label = text or placeholder or aria_label
            if not label:
                continue
            label = label.strip()
            if not label:
                continue

            if label in _seen:
                continue
            _seen.add(label)

            type_label = tag
            if tag == "a":
                type_label = "链接"
            elif tag == "input":
                type_label = "输入框"
            elif tag == "select":
                type_label = "下拉框"
            elif tag == "button":
                type_label = "按钮"
            elif tag == "textarea":
                type_label = "文本域"

            _available.append(f"  [{type_label}] {label}")
    elif isinstance(dom_data, str):
        # 格式2: 文本格式 — page_dom_snapshots
        for _line in dom_data.split("\n"):
            _line = _line.strip()
            if not _line.startswith("["):
                continue

            _tag_m = re.search(r"tag=(\w+)", _line)
            if not _tag_m:
                continue
            _tag = _tag_m.group(1)

            if _tag not in ("button", "input", "a", "select", "textarea"):
                if "role=button" not in _line:
                    continue

            _text_m = re.search(r"text=([^|]+?)(?:\s*\||\s*$)", _line)
            _ph_m = re.search(r"placeholder=([^|]+?)(?:\s*\||\s*$)", _line)
            _aria_m = re.search(r"ariaLabel=([^|]+?)(?:\s*\||\s*$)", _line)

            _label = ""
            if _text_m:
                _label = _text_m.group(1).strip()
            elif _ph_m:
                _label = _ph_m.group(1).strip()
            elif _aria_m:
                _label = _aria_m.group(1).strip()

            if not _label:
                continue

            if _label in _seen:
                continue
            _seen.add(_label)

            _type_label = _tag
            if _tag == "a":
                _type_label = "链接"
            elif _tag == "input":
                _type_label = "输入框"
            elif _tag == "select":
                _type_label = "下拉框"
            elif _tag == "button":
                _type_label = "按钮"
            elif _tag == "textarea":
                _type_label = "文本域"

            _available.append(f"  [{_type_label}] {_label}")

    if not _available:
        return ""

    return "\n".join(_available[:20])
