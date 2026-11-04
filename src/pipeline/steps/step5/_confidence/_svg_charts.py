# -*- coding: utf-8 -*-
"""
SVG 图表生成（学习曲线 / 缓存仪表盘）
"""
import logging

_log = logging.getLogger(__name__)

from src.pipeline.steps.step5._svg import _svg_line_chart, _svg_bar_chart  # noqa: E402


def _generate_learning_curve_svg(trend_data: list) -> str:
    """
    根据 metrics_collector.get_confidence_trend(project_name) 的历史数据
    绘制学习曲线折线图（avg_confidence 与 pass_rate 两条线）。
    trend_data 元素: {run_id, ts, avg_confidence, pass_rate, block_rate}
    """
    try:
        if not trend_data or not isinstance(trend_data, list):
            return '<span style="color:#888">暂无历史数据</span>'
        data = [d for d in trend_data if isinstance(d, dict)]
        if not data:
            return '<span style="color:#888">暂无历史数据</span>'

        def _sort_key(d):
            return d.get("ts") or d.get("run_id") or ""

        data.sort(key=_sort_key)

        def _xlabel(d, idx):
            rid = str(d.get("run_id") or ("#%d" % (idx + 1)))
            return rid[-8:] if len(rid) > 8 else rid

        conf_series = {
            "label": "平均置信度",
            "points": [
                {"x_label": _xlabel(d, i), "y": d.get("avg_confidence")}
                for i, d in enumerate(data)
            ],
        }
        pass_series = {
            "label": "执行通过率",
            "points": [
                {"x_label": _xlabel(d, i), "y": d.get("pass_rate")}
                for i, d in enumerate(data)
            ],
        }
        svg = _svg_line_chart(
            "📈 学习曲线 — 近 %d 次执行" % len(data),
            [conf_series, pass_series],
            width=520,
            height=200,
        )
        if svg:
            return svg
        return '<span style="color:#888">暂无历史数据</span>'
    except Exception as _te:
        _log.debug("  [SVG] _generate_learning_curve_svg 失败: %s", _te)
        return '<span style="color:#888">暂无历史数据</span>'


def _generate_cache_dashboard_svg(cache_stats: dict) -> str:
    """
    根据 op_cache.get_stats(project_name) 返回的缓存统计，生成两个小型水平条形图。
    兼容字段：hit/miss/hit_rate/total_entries/top_hits 或 session_hits/session_misses/session_rate。
    """
    try:
        if not isinstance(cache_stats, dict) or not cache_stats:
            return '<span style="color:#888">暂无缓存统计</span>'
        hits = cache_stats.get("hit")
        if hits is None:
            hits = cache_stats.get("session_hits")
        misses = cache_stats.get("miss")
        if misses is None:
            misses = cache_stats.get("session_misses")
        hit_rate = cache_stats.get("hit_rate")
        if hit_rate is None:
            hit_rate = cache_stats.get("session_rate")
        total_entries = cache_stats.get("total_entries")
        top_hits = cache_stats.get("top_hits") or []
        has_any = (hits is not None) or (misses is not None) or (hit_rate is not None)
        if not has_any:
            return '<span style="color:#888">暂无缓存统计</span>'
        parts = []
        if hit_rate is not None:
            try:
                hr_val = float(hit_rate)
            except (TypeError, ValueError):
                hr_val = None
            if hr_val is not None:
                if 0.0 <= hr_val <= 1.0:
                    hr_val = hr_val * 100.0
                svg1 = _svg_bar_chart(
                    "缓存命中率",
                    [{"label": "Hit Rate", "value": hr_val, "max_val": 100}],
                    width=340,
                    height=90,
                )
                if svg1:
                    parts.append(svg1)
        if hits is not None or misses is not None:
            items_compare = []
            try:
                items_compare.append(
                    {
                        "label": "命中 (Hit)",
                        "value": float(hits if hits is not None else 0),
                    }
                )
                items_compare.append(
                    {
                        "label": "未命中 (Miss)",
                        "value": float(misses if misses is not None else 0),
                    }
                )
            except (TypeError, ValueError):
                items_compare = []
            if (
                items_compare
                and items_compare[0]["value"] + items_compare[1]["value"] > 0
            ):
                svg2 = _svg_bar_chart(
                    "命中 / 未命中", items_compare, width=340, height=110
                )
                if svg2:
                    parts.append(svg2)
        text_lines = []
        if total_entries is not None:
            try:
                te = int(float(total_entries))
                text_lines.append(
                    '<div style="color:#333;font-family:Microsoft YaHei,Arial;'
                    'font-size:12px;margin:6px 0 4px 0;">缓存条目总数: <b>%d</b></div>'
                    % te
                )
            except (TypeError, ValueError) as _e:
                _log.debug("[svg_charts] total_entries 解析异常: %s", _e, exc_info=True)
        if isinstance(top_hits, list) and top_hits:
            items = []
            for h in top_hits[:5]:
                if isinstance(h, dict):
                    k = str(h.get("key") or h.get("cache_key") or "")
                    try:
                        c = int(h.get("hits") or h.get("count") or 0)
                    except (TypeError, ValueError):
                        c = 0
                    if k or c:
                        k_short = k[:36] + ("…" if len(k) > 36 else "")
                        items.append((k_short, c))
            if items:
                html_rows = "".join(
                    '<tr><td style="padding:2px 8px;">%s</td>'
                    '<td style="padding:2px 8px;text-align:right;">%d</td></tr>'
                    % (k, c)
                    for k, c in items
                )
                text_lines.append(
                    '<div style="color:#333;font-family:Microsoft YaHei,Arial;'
                    'font-size:12px;margin:6px 0 4px 0;">Top HIT 缓存键:</div>'
                    '<table style="border-collapse:collapse;font-size:12px;'
                    'background:#fafbfc;border:1px solid #e2e6ec;border-radius:4px;">'
                    '<thead><tr><th style="padding:4px 8px;text-align:left;">Key</th>'
                    '<th style="padding:4px 8px;text-align:right;">Hits</th></tr></thead>'
                    "<tbody>%s</tbody></table>" % html_rows
                )
        if not parts and not text_lines:
            return '<span style="color:#888">暂无缓存统计</span>'
        return (
            '<div style="display:flex;flex-wrap:wrap;align-items:flex-start;'
            'gap:12px;">%s</div>%s'
        ) % ("".join(parts), "".join(text_lines))
    except Exception as _ce:
        _log.debug("  [SVG] _generate_cache_dashboard_svg 失败: %s", _ce)
        return '<span style="color:#888">暂无缓存统计</span>'
