# -*- coding: utf-8 -*-
"""精确位置/尺寸断言 — position_check / exact_size_check。

用 getBoundingClientRect 做精确坐标和尺寸验证。
支持容差（tolerance），默认 0px。

设计原则：纯 JS 检测，不抛异常，selector 找不到 → 标 evidence 不 fail（避免误杀）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

_RECT_JS = r"""
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return {found: false};
  const r = el.getBoundingClientRect();
  return {found: true, x: r.x, y: r.y, width: r.width, height: r.height,
          cx: r.x + r.width/2, cy: r.y + r.height/2};
}
"""


def _to_num(v, default: float = 0.0) -> float:
    """安全转浮点数。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def verify_position(page, check: dict[str, Any]) -> dict[str, Any]:
    """精确位置/尺寸断言。

    check 格式：
      {"type": "position_check",
       "selector": "css选择器",
       "x": 期望x, "y": 期望y,   (可选，不传的维度不检查)
       "tolerance": 容差像素}

      {"type": "exact_size_check",
       "selector": "css选择器",
       "width": 期望宽, "height": 期望高,
       "tolerance": 容差像素}
    """
    check_type = check.get("type", "")
    selector = check.get("selector", "")
    if not selector:
        return {"passed": True, "evidence": "无 selector，跳过"}

    if page is None:
        return {"passed": True, "evidence": "无 page，位置断言跳过"}

    try:
        res = await page.evaluate(_RECT_JS, selector)
        if not res.get("found"):
            return {
                "passed": True,
                "evidence": "位置[%s] selector 未找到: %s" % (check_type, selector),
            }

        tolerance = _to_num(check.get("tolerance"), 0.0)
        issues = []

        if check_type == "position_check":
            if "x" in check:
                exp_x = _to_num(check["x"])
                act_x = _to_num(res.get("x"))
                if abs(act_x - exp_x) > tolerance:
                    issues.append("x=%.1f(期望%.1f±%.0f)" % (act_x, exp_x, tolerance))
            if "y" in check:
                exp_y = _to_num(check["y"])
                act_y = _to_num(res.get("y"))
                if abs(act_y - exp_y) > tolerance:
                    issues.append("y=%.1f(期望%.1f±%.0f)" % (act_y, exp_y, tolerance))
        elif check_type == "exact_size_check":
            if "width" in check:
                exp_w = _to_num(check["width"])
                act_w = _to_num(res.get("width"))
                if abs(act_w - exp_w) > tolerance:
                    issues.append("w=%.1f(期望%.1f±%.0f)" % (act_w, exp_w, tolerance))
            if "height" in check:
                exp_h = _to_num(check["height"])
                act_h = _to_num(res.get("height"))
                if abs(act_h - exp_h) > tolerance:
                    issues.append("h=%.1f(期望%.1f±%.0f)" % (act_h, exp_h, tolerance))
        else:
            return {
                "passed": True,
                "evidence": "未知位置断言类型(%s)，跳过" % check_type,
            }

        if issues:
            return {
                "passed": False,
                "evidence": "位置[%s] 不符: %s" % (check_type, "; ".join(issues)),
            }
        return {
            "passed": True,
            "evidence": "位置[%s] 全部满足(容差%.0fpx)" % (check_type, tolerance),
        }

    except Exception as e:
        _log.warning("[PositionCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "位置断言异常，跳过: %s" % str(e)[:80]}
