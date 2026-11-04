# -*- coding: utf-8 -*-
"""视觉（visual）维度 step5 报告适配层（薄编排，零业务逻辑）。

闭环::

    step4 ``cross_cut_gates`` 采集 run 级 visual 门禁（VLM / 规则本地降级）
        → step4 ``write_visual_review`` 落库 visual_review（run 级 execution_id=0）
        → 本模块 ``build_visual_report`` 从 visual_review 聚合
        → ``render_visual_section`` 渲染 Markdown

与 a11y（``_a11y_report.py``）同构：纯「汇总统计」展示维度，不做跨 run 基线对比。
默认不启用；经 ``report_generator`` 的 ``VISUAL_REPORT_ENABLED`` 开关接入。

口径说明（诚实，不伪造）：
  - visual_review 表无 run_id 列，每次 run 落一条 run 级独立复核（execution_id=0）；
    故按 project 取「最近」的 run 级记录聚合，标题标注「最近视觉复核」，不谎称与
    任意指定 run 精确对齐。
  - 任一异常只 warning，绝不阻断报告生成（整体 try-except 兜底）。

[2026-09-03 新增：补全三横切维度展示，消除 visual 报告空缺]
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# vlm_diff.status 到中文语义标签
_STATUS_LABELS = {
    "pass": "通过",
    "fail": "未通过",
    "no_data": "无数据",
    "skip": "跳过",
    "skipped": "跳过",
}
_STATUS_ORDER = ["通过", "未通过", "跳过", "无数据"]


def _norm_status(raw: Any) -> str:
    """vlm_diff.status 归一化为 pass/fail/skip/no_data。"""
    s = str(raw or "").strip().upper()
    if s in ("PASS", "OK", "CONSISTENT"):
        return "pass"
    if s in ("FAIL", "INCONSISTENT", "DIFF"):
        return "fail"
    if s in ("SKIP", "SKIPPED"):
        return "skip"
    return "no_data"


def _fetch_visual_rows(project: str, limit: int = 50) -> list[dict]:
    """从 visual_review 拉取该 project 最近的 run 级视觉复核记录。"""
    project = str(project or "").strip()
    if not project:
        return []
    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            rows = (
                conn.execute(
                    "SELECT v.version_id, v.execution_id, v.screenshot_path, "
                    "       v.vlm_diff, v.created_at "
                    "FROM visual_review v "
                    "WHERE v.project=%s AND v.execution_id=0 "
                    "ORDER BY v.id DESC LIMIT %s",
                    (project, int(limit)),
                ).fetchall()
                or []
            )
            out = []
            for r in rows:
                diff = r.get("vlm_diff")
                if isinstance(diff, str):
                    try:
                        diff = json.loads(diff)
                    except (ValueError, TypeError):
                        diff = {}
                diff = diff if isinstance(diff, dict) else {}
                out.append(
                    {
                        "version_id": r.get("version_id"),
                        "screenshot_path": r.get("screenshot_path") or "",
                        "created_at": str(r.get("created_at") or ""),
                        "status": _norm_status(diff.get("status")),
                        "passed": int(diff.get("passed") or 0),
                        "skipped": int(diff.get("skipped") or 0),
                        "confidence": diff.get("confidence"),
                        "reason": str(diff.get("reason") or "")[:300],
                        "evidence": str(diff.get("evidence") or "")[:300],
                    }
                )
            return out
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("[visual_report] conn.close: %s", _e)
    except Exception as e:  # noqa: BLE001
        _log.warning("[visual_report] 拉取视觉复核数据失败(不阻断): %s", str(e)[:120])
        return []


def build_visual_report(project: str, run_id: str) -> dict[str, Any]:
    """聚合最近视觉复核维度为报告 dict。

    返回:
        ``{records, by_status, pass, fail, skip, no_data, avg_confidence, items}``；
        无任何视觉复核记录时返回 ``{}``。
    """
    rows = _fetch_visual_rows(project)
    if not rows:
        return {}

    by_status = {"pass": 0, "fail": 0, "skip": 0, "no_data": 0}
    conf_sum, conf_cnt = 0.0, 0
    for r in rows:
        st = r["status"] if r["status"] in by_status else "no_data"
        by_status[st] += 1
        c = r.get("confidence")
        if isinstance(c, (int, float)) and 0 <= c <= 1:
            conf_sum += float(c)
            conf_cnt += 1

    return {
        "records": len(rows),
        "pass": by_status["pass"],
        "fail": by_status["fail"],
        "skip": by_status["skip"],
        "no_data": by_status["no_data"],
        "avg_confidence": round(conf_sum / conf_cnt, 2) if conf_cnt else None,
        "items": rows[:30],
    }


def render_visual_section(report: dict[str, Any]) -> str:
    """把 ``build_visual_report`` 结果渲染为 Markdown 区块。"""
    if not report or not report.get("records"):
        return ""

    by = {
        "通过": int(report.get("pass") or 0),
        "未通过": int(report.get("fail") or 0),
        "跳过": int(report.get("skip") or 0),
        "无数据": int(report.get("no_data") or 0),
    }
    _conf = report.get("avg_confidence")
    _conf_s = ("%.2f" % _conf) if _conf is not None else "—"

    lines = [
        "## 视觉复核（Visual Review）",
        "",
        "> VLM 复核（可用模型）失败时引擎 fail-closed 回「规则型本地降级」，"
        "诚实标注 NO_DATA，不伪造结论。",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 复核记录 | %d |" % int(report.get("records") or 0),
        "| 平均置信度 | %s |" % _conf_s,
    ]
    for lbl, n in by.items():
        lines.append("| %s | %d |" % (lbl, n))

    items = report.get("items") or []
    if items:
        lines += [
            "",
            "### 最近复核明细",
            "",
            "| 时间 | 结果 | 通过数 | 跳过数 | 置信度 | 说明 |",
            "|------|------|--------|--------|--------|------|",
        ]
        for it in items:
            lines.append(
                "| %s | %s | %d | %d | %s | %s |"
                % (
                    (it.get("created_at") or "-").replace("T", " ")[:19],
                    _STATUS_LABELS.get(it.get("status", ""), it.get("status", "-")),
                    int(it.get("passed") or 0),
                    int(it.get("skipped") or 0),
                    ("%.2f" % it["confidence"]) if isinstance(
                        it.get("confidence"), (int, float)) else "—",
                    (it.get("reason") or it.get("evidence") or "-").replace("|", "/")[:50],
                )
            )

    return "\n".join(lines)


def build_visual_section(project: str, run_id: str) -> str:
    """供 report_generator 调用的总入口：读库→聚合→渲染，返回 Markdown（空=不渲染）。"""
    try:
        report = build_visual_report(project, run_id)
        return render_visual_section(report)
    except Exception as e:  # noqa: BLE001
        _log.warning("[visual_report] 视觉区块构建失败(不阻断): %s", str(e)[:120])
        return ""