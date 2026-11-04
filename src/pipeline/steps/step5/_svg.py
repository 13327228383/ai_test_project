# -*- coding: utf-8 -*-
"""
SVG 图表生成工具函数
"""
import logging

_log = logging.getLogger(__name__)


def _svg_line_chart(
    title: str, data_series: list, width: int = 520, height: int = 180
) -> str:
    """
    生成多系列折线图 SVG（纯字符串拼接，无任何第三方依赖）。
    data_series: [{label, points: [{x_label, y}]}]
    y 值可以是 0-1 或 0-100，自动按百分比缩放。
    """
    try:
        if not data_series or all(not s.get("points") for s in data_series):
            return ""
        colors = ["#1f77c8", "#2fa36a", "#d18a2b", "#8a4fb0", "#c84b4b", "#4ca5b7"]
        padding = {"top": 28, "right": 16, "bottom": 32, "left": 40}
        plot_w = max(width - padding["left"] - padding["right"], 40)
        plot_h = max(height - padding["top"] - padding["bottom"], 30)

        def _norm_y(y):
            if y is None:
                return None
            try:
                v = float(y)
            except (TypeError, ValueError):
                return None
            if 0.0 <= v <= 1.0:
                return v * 100.0
            if 0.0 <= v <= 100.0:
                return v
            return max(0.0, min(100.0, v))

        x_labels = []
        seen = set()
        for s in data_series:
            for p in s.get("points", []):
                xl = str(p.get("x_label", ""))
                if xl not in seen:
                    seen.add(xl)
                    x_labels.append(xl)
        n = len(x_labels)
        if n == 0:
            return ""

        def _xi(i):
            if n == 1:
                return padding["left"] + plot_w / 2
            return padding["left"] + plot_w * i / (n - 1)

        def _yi(y):
            return padding["top"] + plot_h * (1 - y / 100.0)

        y_ticks = [0, 25, 50, 75, 100]
        grid_lines = []
        y_labels = []
        for t in y_ticks:
            yy = _yi(t)
            grid_lines.append(
                '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                'stroke="#e2e6ec" stroke-width="1" stroke-dasharray="3,3"/>'
                % (padding["left"], yy, padding["left"] + plot_w, yy)
            )
            y_labels.append(
                '<text x="%d" y="%.1f" fill="#666" font-size="10" '
                'font-family="Arial" text-anchor="end" dominant-baseline="middle">%d</text>'
                % (padding["left"] - 4, yy, t)
            )
        max_x_labels = min(6, n)
        step = max(1, (n + max_x_labels - 1) // max_x_labels)
        x_label_elems = []
        for i in range(0, n, step):
            xl = x_labels[i]
            short = xl[-6:] if len(xl) > 6 else xl
            xx = _xi(i)
            x_label_elems.append(
                '<text x="%.1f" y="%d" fill="#666" font-size="10" '
                'font-family="Arial" text-anchor="middle">%s</text>'
                % (xx, padding["top"] + plot_h + 16, short)
            )
        axis_lines = (
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#888" stroke-width="1"/>'
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#888" stroke-width="1"/>'
        ) % (
            padding["left"],
            padding["top"],
            padding["left"],
            padding["top"] + plot_h,
            padding["left"],
            padding["top"] + plot_h,
            padding["left"] + plot_w,
            padding["top"] + plot_h,
        )
        idx_map = {xl: i for i, xl in enumerate(x_labels)}
        series_paths = []
        series_dots = []
        legend_items = []
        for si, s in enumerate(data_series):
            color = colors[si % len(colors)]
            label = str(s.get("label", "系列 %d" % (si + 1)))
            pts = s.get("points", [])
            xy = []
            for p in pts:
                xl = str(p.get("x_label", ""))
                if xl not in idx_map:
                    continue
                y = _norm_y(p.get("y"))
                if y is None:
                    continue
                xy.append((_xi(idx_map[xl]), _yi(y)))
            if not xy:
                continue
            path_d = "M " + " L ".join("%.1f,%.1f" % (x, y) for x, y in xy)
            series_paths.append(
                '<path d="%s" fill="none" stroke="%s" stroke-width="2" '
                'stroke-linejoin="round" stroke-linecap="round"/>' % (path_d, color)
            )
            for x, y in xy:
                series_dots.append(
                    '<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/>' % (x, y, color)
                )
            lx = 16 + si * 120
            legend_items.append(
                '<g><line x1="%d" y1="16" x2="%d" y2="16" stroke="%s" '
                'stroke-width="2"/><circle cx="%d" cy="16" r="3" fill="%s"/>'
                '<text x="%d" y="19" fill="#333" font-size="11" '
                'font-family="Arial">%s</text></g>'
                % (lx, lx + 18, color, lx + 9, color, lx + 24, label)
            )
        title_safe = (title or "").replace("<", "&lt;").replace(">", "&gt;")
        title_elem = (
            '<text x="%d" y="16" fill="#222" font-size="13" '
            'font-weight="bold" font-family="Microsoft YaHei,Arial">%s</text>'
            % (padding["left"], title_safe)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" style="background:#fff;border:1px solid #e2e6ec;'
            'border-radius:6px;display:inline-block;max-width:100%%;">'
            "%s%s%s%s%s%s%s%s"
            "</svg>"
        ) % (
            width,
            height,
            width,
            height,
            title_elem,
            "".join(legend_items),
            "".join(grid_lines),
            axis_lines,
            "".join(y_labels),
            "".join(x_label_elems),
            "".join(series_paths),
            "".join(series_dots),
        )
        return svg
    except Exception as _le:
        _log.debug("  [SVG] _svg_line_chart 失败: %s", _le)
        return ""


def _svg_bar_chart(title: str, items: list, width: int = 520, height: int = 180) -> str:
    """
    生成水平条形图 SVG。
    items: [{label, value, max_val?}]
    value 必须为数字；max_val 可选，缺省时取 items 中最大 value。
    """
    try:
        if not items:
            return ""
        numeric_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                v = float(it.get("value"))
            except (TypeError, ValueError):
                continue
            numeric_items.append(
                {
                    "label": str(it.get("label", "")),
                    "value": v,
                    "max_val": it.get("max_val"),
                }
            )
        if not numeric_items:
            return ""
        bar_h = 18
        gap = 8
        pad_top = 30
        pad_bottom = 16
        pad_left = 110
        pad_right = 60
        n = len(numeric_items)
        inner_h = n * (bar_h + gap) - gap
        plot_h = max(inner_h, 40)
        total_h = pad_top + plot_h + pad_bottom
        total_w = width
        plot_w = max(total_w - pad_left - pad_right, 40)
        explicit_max = any(it.get("max_val") is not None for it in numeric_items)
        if not explicit_max:
            global_max = max(it["value"] for it in numeric_items)
            for it in numeric_items:
                it["_max"] = global_max if global_max > 0 else 1.0
        else:
            for it in numeric_items:
                try:
                    it["_max"] = float(it.get("max_val")) or 1.0
                except (TypeError, ValueError):
                    it["_max"] = 1.0
                if it["_max"] <= 0:
                    it["_max"] = 1.0
        colors = ["#1f77c8", "#2fa36a", "#d18a2b", "#8a4fb0", "#c84b4b", "#4ca5b7"]
        title_safe = (title or "").replace("<", "&lt;").replace(">", "&gt;")
        title_elem = (
            '<text x="%d" y="18" fill="#222" font-size="13" '
            'font-weight="bold" font-family="Microsoft YaHei,Arial">%s</text>'
            % (pad_left, title_safe)
        )
        bar_elems = []
        label_elems = []
        value_elems = []
        for i, it in enumerate(numeric_items):
            y = pad_top + i * (bar_h + gap)
            label = it["label"] or ""
            short = label[:14] + ("…" if len(label) > 14 else "")
            pct = it["value"] / it["_max"]
            pct = max(0.0, min(1.0, pct))
            bar_w = plot_w * pct
            color = colors[i % len(colors)]
            if abs(it["_max"] - 100.0) < 1e-6:
                val_text = "%.1f%%" % it["value"]
            else:
                val_text = (
                    ("%d" % it["value"])
                    if it["value"] == int(it["value"])
                    else ("%.2f" % it["value"])
                )
            bar_elems.append(
                '<rect x="%d" y="%d" width="%.1f" height="%d" fill="%s" rx="3" ry="3"/>'
                % (pad_left, y, bar_w, bar_h, color)
            )
            label_elems.append(
                '<text x="%d" y="%d" fill="#333" font-size="11" '
                'font-family="Microsoft YaHei,Arial" text-anchor="end" '
                'dominant-baseline="middle">%s</text>'
                % (pad_left - 6, y + bar_h / 2, short)
            )
            value_elems.append(
                '<text x="%.1f" y="%d" fill="#333" font-size="11" '
                'font-family="Arial" text-anchor="start" dominant-baseline="middle"> %s</text>'
                % (pad_left + bar_w, y + bar_h / 2, val_text)
            )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" style="background:#fff;border:1px solid #e2e6ec;'
            'border-radius:6px;display:inline-block;max-width:100%%;margin-right:8px;">'
            "%s%s%s%s"
            "</svg>"
        ) % (
            total_w,
            total_h,
            total_w,
            total_h,
            title_elem,
            "".join(label_elems),
            "".join(bar_elems),
            "".join(value_elems),
        )
        return svg
    except Exception as _be:
        _log.debug("  [SVG] _svg_bar_chart 失败: %s", _be)
        return ""
