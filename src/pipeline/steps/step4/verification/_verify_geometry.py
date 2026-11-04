# -*- coding: utf-8 -*-
"""几何断言（getBoundingClientRect，多点采样，容差支持）。

场景2 视觉兜底：vision 模型不可用时，用纯 JS 几何检测布局类缺陷。
不依赖 vision，纯 Playwright page.evaluate。

规则：
- no_overlap: selectors 元素两两矩形相交面积 > min_overlap_area → fail（如柱子遮挡坐标轴）
- in_viewport: 元素可见比例 < min_visible_ratio → fail（越界/被裁切）
- not_occluded: 目标元素四角+中心被其他元素遮挡 → fail（多点采样防误判）
- min_size: 元素宽高 < min_w/min_h → fail（零尺寸/隐藏元素）
- distance: 两元素中心距离不在 [min, max] 区间 → fail（间距检测）

设计原则：纯几何，不抛异常，任一 selector 找不到 → 判定 fail(避免假通过掩盖布局问题)。
"""

import logging
import math
from typing import Any

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS 注入段 — 取所有匹配元素的 boundingClientRect + 视口尺寸
# ═══════════════════════════════════════════════════════════════
_RECTS_JS = r"""
(selectors) => {
  const out = {rects: [], viewport: {w: innerWidth, h: innerHeight}, missing: []};
  for (const sel of selectors) {
    const els = document.querySelectorAll(sel);
    if (!els.length) { out.missing.push(sel); continue; }
    for (let idx = 0; idx < els.length; idx++) {
      const el = els[idx];
      const r = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      out.rects.push({
        selector: sel, index: idx,
        x: r.x, y: r.y, w: r.width, h: r.height,
        cx: r.x + r.width/2, cy: r.y + r.height/2,
        display: style.display, visibility: style.visibility, opacity: parseFloat(style.opacity)
      });
    }
  }
  return out;
}
"""

# not_occluded：四角 + 中心采样，任一采样点被遮挡 → 被遮挡
_OCCLUDE_JS = r"""
(selectors) => {
  const out = {occluded: [], missing: []};
  for (const sel of selectors) {
    const els = document.querySelectorAll(sel);
    if (!els.length) { out.missing.push(sel); continue; }
    for (let idx = 0; idx < els.length; idx++) {
      const el = els[idx];
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;  // 零尺寸元素不检测遮挡
      // 采样点：中心 + 四角内偏 2px（避免刚好在边框上）
      const inset = 2;
      const points = [
        [r.x + r.width/2,  r.y + r.height/2],           // 中心
        [r.x + inset,      r.y + inset],                // 左上
        [r.x + r.width - inset, r.y + inset],           // 右上
        [r.x + inset,      r.y + r.height - inset],     // 左下
        [r.x + r.width - inset, r.y + r.height - inset] // 右下
      ];
      const blocked = [];
      for (const [px, py] of points) {
        if (px < 0 || py < 0 || px > innerWidth || py > innerHeight) continue; // 视口外忽略
        const top = document.elementFromPoint(px, py);
        if (top && top !== el && !el.contains(top) && !top.contains(el)) {
          blocked.push({px: Math.round(px), py: Math.round(py), by: top.tagName + (top.className ? '.'+String(top.className).slice(0,20) : '')});
        }
      }
      // 至少 3 个采样点被遮挡才判定为 occluded（防边缘误报）
      if (blocked.length >= 3) {
        out.occluded.push({selector: sel, index: idx, blocked: blocked});
      }
    }
  }
  return out;
}
"""

# ═══════════════════════════════════════════════════════════════
# Python 几何工具
# ═══════════════════════════════════════════════════════════════


def _intersect_area(a: dict, b: dict) -> float:
    """两矩形相交面积。"""
    x_overlap = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    y_overlap = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return x_overlap * y_overlap


def _visible_ratio(r: dict, vp: dict) -> float:
    """元素在视口内的可见比例（面积比）。"""
    el_area = r["w"] * r["h"]
    if el_area <= 0:
        return 0.0
    x_overlap = max(0, min(r["x"] + r["w"], vp["w"]) - max(r["x"], 0))
    y_overlap = max(0, min(r["y"] + r["h"], vp["h"]) - max(r["y"], 0))
    visible_area = x_overlap * y_overlap
    return visible_area / el_area


def _center_distance(a: dict, b: dict) -> float:
    """两元素中心点欧氏距离。"""
    return math.sqrt((a["cx"] - b["cx"]) ** 2 + (a["cy"] - b["cy"]) ** 2)


def _to_num(v, default: float = 0.0) -> float:
    """安全转浮点数。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════


async def verify_geometry(page, check: dict[str, Any]) -> dict[str, Any]:
    """几何断言。

    check 格式：
      {"type": "geometry_check",
       "rule": "no_overlap|in_viewport|not_occluded|min_size|distance",
       "selectors": ["css1", "css2"],
       ... rule 专属参数}

    返回：{"passed": bool, "evidence": str, "detail": dict(可选调试数据)}
    """
    rule = (check.get("rule") or "").strip()
    selectors = check.get("selectors") or []
    if not isinstance(selectors, list) or len(selectors) < 1:
        return {"passed": True, "evidence": "geometry_check 无 selectors，跳过"}
    valid_rules = ("no_overlap", "in_viewport", "not_occluded", "min_size", "distance")
    if rule not in valid_rules:
        return {"passed": True, "evidence": "geometry_check 未知 rule(%s)，跳过" % rule}

    if page is None:
        return {"passed": True, "evidence": "无 page，几何断言跳过"}

    detail = {"rule": rule, "selectors": selectors}

    try:
        # ── not_occluded：专用 JS，多点采样 ──
        if rule == "not_occluded":
            res = await page.evaluate(_OCCLUDE_JS, selectors)
            missing = res.get("missing", [])
            occluded = res.get("occluded", [])
            detail["missing"] = missing
            detail["occluded"] = occluded
            if missing:
                return {
                    "passed": False,
                    "evidence": "几何[not_occluded] selector 未找到: %s"
                    % ",".join(missing),
                    "detail": detail,
                }
            if occluded:
                items = []
                for o in occluded:
                    pts = ";".join(
                        "(%s,%s)→%s" % (b["px"], b["py"], b["by"])
                        for b in o["blocked"][:2]
                    )
                    items.append("%s[%d]@%s" % (o["selector"], o["index"], pts))
                return {
                    "passed": False,
                    "evidence": "几何[not_occluded] 遮挡: %s" % " | ".join(items),
                    "detail": detail,
                }
            return {
                "passed": True,
                "evidence": "几何[not_occluded] 无遮挡",
                "detail": detail,
            }

        # ── 其余规则：统一取 rects ──
        res = await page.evaluate(_RECTS_JS, selectors)
        rects: list[dict] = res.get("rects", [])
        missing = res.get("missing", [])
        viewport = res.get("viewport", {"w": 0, "h": 0})
        detail["rects"] = rects
        detail["missing"] = missing
        detail["viewport"] = viewport

        if missing:
            # ★ T-004: selector 未找到应判定为失败(与 not_occluded 一致),避免假通过掩盖布局问题
            return {
                "passed": False,
                "evidence": "几何[%s] selector 未找到: %s" % (rule, ",".join(missing)),
                "detail": detail,
            }
        if not rects:
            return {
                "passed": False,
                "evidence": "几何[%s] 无有效元素" % rule,
                "detail": detail,
            }

        # ── no_overlap ──
        if rule == "no_overlap":
            if len(rects) < 2:
                return {
                    "passed": True,
                    "evidence": "几何[no_overlap] 仅%d元素，跳过" % len(rects),
                    "detail": detail,
                }
            min_overlap_area = _to_num(check.get("min_overlap_area"), 0.0)
            overlaps = []
            for i, rect_a in enumerate(rects):
                for j in range(i + 1, len(rects)):
                    area = _intersect_area(rect_a, rects[j])
                    if area > min_overlap_area:
                        overlaps.append(
                            "%s[%d]∩%s[%d]=%.0fpx²"
                            % (
                                rect_a["selector"],
                                rect_a["index"],
                                rects[j]["selector"],
                                rects[j]["index"],
                                area,
                            )
                        )
            if overlaps:
                return {
                    "passed": False,
                    "evidence": "几何[no_overlap] 相交: %s" % "; ".join(overlaps),
                    "detail": detail,
                }
            return {
                "passed": True,
                "evidence": "几何[no_overlap] 无相交",
                "detail": detail,
            }

        # ── in_viewport ──
        if rule == "in_viewport":
            min_visible_ratio = _to_num(check.get("min_visible_ratio"), 1.0)
            out_of = []
            for r in rects:
                ratio = _visible_ratio(r, viewport)
                if ratio < min_visible_ratio:
                    out_of.append(
                        "%s[%d]可见%.0f%%(阈值%.0f%%)"
                        % (
                            r["selector"],
                            r["index"],
                            ratio * 100,
                            min_visible_ratio * 100,
                        )
                    )
            if out_of:
                return {
                    "passed": False,
                    "evidence": "几何[in_viewport] 越界/裁切: %s" % "; ".join(out_of),
                    "detail": detail,
                }
            return {
                "passed": True,
                "evidence": "几何[in_viewport] 全部满足可见比例",
                "detail": detail,
            }

        # ── min_size ──
        if rule == "min_size":
            min_w = _to_num(check.get("min_w"), 1.0)
            min_h = _to_num(check.get("min_h"), 1.0)
            too_small = []
            for r in rects:
                issues = []
                if r["w"] < min_w:
                    issues.append("w=%.0f<%.0f" % (r["w"], min_w))
                if r["h"] < min_h:
                    issues.append("h=%.0f<%.0f" % (r["h"], min_h))
                if issues:
                    too_small.append(
                        "%s[%d]:%s" % (r["selector"], r["index"], ",".join(issues))
                    )
            if too_small:
                return {
                    "passed": False,
                    "evidence": "几何[min_size] 尺寸不足: %s" % "; ".join(too_small),
                    "detail": detail,
                }
            return {
                "passed": True,
                "evidence": "几何[min_size] 全部满足",
                "detail": detail,
            }

        # ── distance ──
        if rule == "distance":
            if len(rects) < 2:
                return {
                    "passed": True,
                    "evidence": "几何[distance] 仅%d元素，跳过" % len(rects),
                    "detail": detail,
                }
            min_dist = _to_num(check.get("min"), 0.0)
            max_dist = _to_num(check.get("max"), float("inf"))
            bad = []
            for i, rect_a in enumerate(rects):
                for j in range(i + 1, len(rects)):
                    d = _center_distance(rect_a, rects[j])
                    if d < min_dist or d > max_dist:
                        bad.append(
                            "%s[%d]↔%s[%d]=%.0fpx(区间%.0f~%.0f)"
                            % (
                                rect_a["selector"],
                                rect_a["index"],
                                rects[j]["selector"],
                                rects[j]["index"],
                                d,
                                min_dist,
                                max_dist,
                            )
                        )
            if bad:
                return {
                    "passed": False,
                    "evidence": "几何[distance] 间距不符: %s" % "; ".join(bad),
                    "detail": detail,
                }
            return {
                "passed": True,
                "evidence": "几何[distance] 全部满足",
                "detail": detail,
            }

    except Exception as e:
        _log.warning("[GeometryCheck] 异常: %s", str(e)[:120], exc_info=True)
        detail["error"] = str(e)
        return {
            "passed": False,
            "evidence": "几何断言异常: %s" % str(e)[:80],
            "detail": detail,
        }

    return {"passed": True, "evidence": "几何[%s] 未处理" % rule, "detail": detail}
