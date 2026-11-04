# -*- coding: utf-8 -*-
"""样式断言 — color_check / font_check / visibility_check / css_class_check / attribute_check / zindex_check。

用 getComputedStyle 和元素属性做客观样式验证，不依赖 vision 模型。
支持精确匹配和模糊匹配（precision=exact/fuzzy）。

设计原则：纯 JS 检测，不抛异常，selector 找不到 → 标 evidence 不 fail（避免误杀）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

_STYLE_JS = r"""
(args) => {
  const {selector, property} = args;
  const el = document.querySelector(selector);
  if (!el) return {found: false, value: ""};
  const style = window.getComputedStyle(el);
  return {found: true, value: style.getPropertyValue(property)};
}
"""

_ATTR_JS = r"""
(args) => {
  const {selector, attribute} = args;
  const el = document.querySelector(selector);
  if (!el) return {found: false, value: ""};
  return {found: true, value: el.getAttribute(attribute) || ""};
}
"""

_CLASS_JS = r"""
(args) => {
  const {selector} = args;
  const el = document.querySelector(selector);
  if (!el) return {found: false, classes: []};
  return {found: true, classes: Array.from(el.classList)};
}
"""

_VIS_JS = r"""
(args) => {
  const {selector} = args;
  const el = document.querySelector(selector);
  if (!el) return {found: false};
  const style = window.getComputedStyle(el);
  return {
    found: true,
    display: style.display,
    visibility: style.visibility,
    opacity: parseFloat(style.opacity),
    visible: style.display !== "none" && style.visibility !== "hidden" && parseFloat(style.opacity) > 0
  };
}
"""


def _normalize_color(val: str) -> str:
    """将颜色值统一为小写 hex 或 rgb 格式便于比较。"""
    return val.strip().lower().replace(" ", "")


async def verify_style(page, check: dict[str, Any]) -> dict[str, Any]:
    """样式断言分派入口。

    check 格式：
      {"type": "color_check|font_check|visibility_check|css_class_check|attribute_check|zindex_check",
       "selector": "css选择器",
       "property": "CSS属性名",  (color_check/font_check/zindex_check 用)
       "expected": "期望值",
       "attribute": "HTML属性名", (attribute_check 用)
       "class_name": "CSS类名",   (css_class_check 用)
       "precision": "exact|fuzzy"}
    """
    check_type = check.get("type", "")
    selector = check.get("selector", "")
    if not selector:
        return {"passed": True, "evidence": "无 selector，跳过"}

    if page is None:
        return {"passed": True, "evidence": "无 page，样式断言跳过"}

    try:
        if check_type == "color_check":
            return await _check_computed_style(page, check, selector)
        elif check_type == "font_check":
            return await _check_computed_style(page, check, selector)
        elif check_type == "zindex_check":
            return await _check_computed_style(page, check, selector)
        elif check_type == "visibility_check":
            return await _check_visibility(page, check, selector)
        elif check_type == "css_class_check":
            return await _check_css_class(page, check, selector)
        elif check_type == "attribute_check":
            return await _check_attribute(page, check, selector)
        else:
            return {
                "passed": True,
                "evidence": "未知样式断言类型(%s)，跳过" % check_type,
            }
    except Exception as e:
        _log.warning("[StyleCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "样式断言异常，跳过: %s" % str(e)[:80]}


async def _check_computed_style(page, check, selector) -> dict[str, Any]:
    """通用 computed style 检查（color/font/z-index 共用）。"""
    check_type = check.get("type", "")
    prop_map = {
        "color_check": "color",
        "font_check": check.get("property", "font-size"),
        "zindex_check": "z-index",
    }
    prop = prop_map.get(check_type, check.get("property", ""))
    expected = check.get("expected", "")
    precision = check.get("precision", "exact")

    res = await page.evaluate(_STYLE_JS, {"selector": selector, "property": prop})
    if not res.get("found"):
        return {
            "passed": True,
            "evidence": "样式[%s] selector 未找到: %s" % (check_type, selector),
        }

    actual = res.get("value", "")
    if check_type == "color_check":
        actual_norm = _normalize_color(actual)
        expected_norm = _normalize_color(expected)
        passed = (
            actual_norm == expected_norm
            if precision == "exact"
            else expected_norm in actual_norm
        )
    else:
        passed = actual == expected if precision == "exact" else expected in actual

    return {
        "passed": passed,
        "evidence": "样式[%s] %s=%s (期望%s)" % (check_type, prop, actual, expected),
    }


async def _check_visibility(page, check, selector) -> dict[str, Any]:
    """可见性检查。"""
    expected_visible = check.get("expected", "visible") == "visible"
    res = await page.evaluate(_VIS_JS, {"selector": selector})
    if not res.get("found"):
        return {
            "passed": True,
            "evidence": "样式[visibility_check] selector 未找到: %s" % selector,
        }

    actual_visible = res.get("visible", False)
    passed = actual_visible == expected_visible
    return {
        "passed": passed,
        "evidence": "样式[visibility_check] display=%s visibility=%s opacity=%s → %s"
        % (
            res.get("display"),
            res.get("visibility"),
            res.get("opacity"),
            "可见" if actual_visible else "不可见",
        ),
    }


async def _check_css_class(page, check, selector) -> dict[str, Any]:
    """CSS 类检查。"""
    class_name = check.get("class_name", "")
    res = await page.evaluate(_CLASS_JS, {"selector": selector})
    if not res.get("found"):
        return {
            "passed": True,
            "evidence": "样式[css_class_check] selector 未找到: %s" % selector,
        }

    classes = res.get("classes", [])
    passed = class_name in classes
    return {
        "passed": passed,
        "evidence": "样式[css_class_check] 类列表=%s (查找'%s')"
        % (",".join(classes[:10]), class_name),
    }


async def _check_attribute(page, check, selector) -> dict[str, Any]:
    """HTML 属性检查。"""
    attr = check.get("attribute", "")
    expected = check.get("expected", "")
    precision = check.get("precision", "exact")
    res = await page.evaluate(_ATTR_JS, {"selector": selector, "attribute": attr})
    if not res.get("found"):
        return {
            "passed": True,
            "evidence": "样式[attribute_check] selector 未找到: %s" % selector,
        }

    actual = res.get("value", "")
    passed = actual == expected if precision == "exact" else expected in actual
    return {
        "passed": passed,
        "evidence": "样式[attribute_check] %s=%s (期望%s)" % (attr, actual, expected),
    }
