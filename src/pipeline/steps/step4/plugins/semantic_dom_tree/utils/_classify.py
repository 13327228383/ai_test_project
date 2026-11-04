# -*- coding: utf-8 -*-
"""
semantic_dom_tree.utils._classify — 元素类型识别与文本提取
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 元素类型常量
# ---------------------------------------------------------------------------
_TYPE_INPUT = "input"
_TYPE_BUTTON = "button"
_TYPE_LINK = "link"
_TYPE_TABLE = "table"
_TYPE_OTHER = "other"


_INPUT_TAGS = {"input", "textarea", "select", "textbox", "password", "search", "email"}
_BUTTON_TAGS = {"button", "submit", "btn", "icon-button"}
_LINK_TAGS = {"a", "anchor", "link"}
_TABLE_TAGS = {"table", "grid", "datagrid", "row", "tbody"}


def _get_text(el: Any) -> str:
    """从一个 DOM 元素提取可读文本（tag/text/placeholder/aria-label/value/title）。"""
    try:
        if not el:
            return ""
        if isinstance(el, str):
            return el
        if isinstance(el, dict):
            parts: list[str] = []
            for key in (
                "text",
                "label",
                "placeholder",
                "aria_label",
                "aria-label",
                "value",
                "title",
                "name",
                "href",
                "tag",
            ):
                v = el.get(key)
                if v:
                    s = str(v).strip()
                    if s and len(s) <= 80:
                        parts.append(s)
            return " / ".join(parts)
        # 退化成 str()
        return str(el)[:120]
    except Exception as e:
        _log.warning("semantic_dom_tree._get_text 异常 %s", e)
        return ""


def _get_tag(el: Any) -> str:
    try:
        if isinstance(el, dict):
            t = el.get("tag") or el.get("type") or el.get("role") or ""
            return str(t).lower()
        return ""
    except Exception as _e:
        _log.debug("[_classify] _get_tag 异常: %s", _e, exc_info=True)
        return ""


def _get_y(el: Any) -> float:
    """从 bbox 字段里提取 y 坐标（支持各种形式）。"""
    try:
        if isinstance(el, dict):
            # 多种写法都尝试
            bbox = el.get("bbox") or el.get("rect") or el.get("bounding_box") or {}
            if isinstance(bbox, dict):
                for k in ("y", "top", "y1"):
                    if k in bbox and bbox[k] is not None:
                        try:
                            return float(bbox[k])
                        except (TypeError, ValueError):
                            continue
            # 也可能直接写在元素顶层
            for k in ("y", "top"):
                if k in el and el[k] is not None:
                    try:
                        return float(el[k])
                    except (TypeError, ValueError):
                        continue
        return 0.0
    except Exception as e:
        _log.warning("semantic_dom_tree._get_y 异常 %s", e)
        return 0.0


def _get_h(el: Any) -> float:
    """提取元素高度（用于估算切分阈值）。"""
    try:
        if isinstance(el, dict):
            bbox = el.get("bbox") or el.get("rect") or el.get("bounding_box") or {}
            if isinstance(bbox, dict):
                for k in ("height", "h"):
                    if k in bbox and bbox[k] is not None:
                        try:
                            return float(bbox[k])
                        except (TypeError, ValueError):
                            continue
            for k in ("height", "h"):
                if k in el and el[k] is not None:
                    try:
                        return float(el[k])
                    except (TypeError, ValueError):
                        continue
        # 默认高度 24（文字行的典型高度）
        return 24.0
    except Exception as e:
        _log.warning("semantic_dom_tree._get_h 异常 %s", e)
        return 24.0


def _classify(el: Any) -> str:
    """返回元素的语义类型: input/button/link/table/other。"""
    try:
        tag = _get_tag(el)
        if tag in _INPUT_TAGS:
            return _TYPE_INPUT
        if tag in _BUTTON_TAGS:
            return _TYPE_BUTTON
        if tag in _LINK_TAGS:
            return _TYPE_LINK
        if tag in _TABLE_TAGS:
            return _TYPE_TABLE
        # 回退到文本关键词判断
        text = _get_text(el).lower()
        if any(
            k in text
            for k in ("输入", "input", "placeholder", "密码", "search", "查询")
        ):
            return _TYPE_INPUT
        if any(
            k in text
            for k in (
                "按钮",
                "button",
                "新增",
                "创建",
                "提交",
                "保存",
                "删除",
                "导出",
                "download",
                "export",
                "确认",
                "confirm",
                "登录",
                "login",
                "同意",
                "approve",
                "驳回",
                "reject",
                "点击",
                "click",
            )
        ):
            return _TYPE_BUTTON
        if any(
            k in text
            for k in (
                "http://",
                "https://",
                "href",
                "link",
                "链接",
                "导航",
                "menu",
                "首页",
                "home",
            )
        ):
            return _TYPE_LINK
        if any(k in text for k in ("table", "表格", "列表", "grid", "row", "行")):
            return _TYPE_TABLE
        return _TYPE_OTHER
    except Exception as e:
        _log.warning("semantic_dom_tree._classify 异常 %s", e)
        return _TYPE_OTHER
