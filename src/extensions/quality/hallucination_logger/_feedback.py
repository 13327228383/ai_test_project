# -*- coding: utf-8 -*-
"""
★ P2-3: 闭环反馈 — 历史失败率查询 & Prompt注入
================================================
基于 hallucination_logger 沉淀的失败样本，查询需求/模块的历史失败率，
并生成可注入 Step2/Step3 Prompt 的反馈片段。
"""

import logging
from collections import Counter
from typing import Any

_log = logging.getLogger(__name__)

from ._logger import _FAILURE_KEYWORDS


# ══════════════════════════════════════════════════════════
# ★ P2-3: 闭环反馈 — 历史失败率查询 & Prompt注入
# ══════════════════════════════════════════════════════════


def get_requirement_failure_history(
    req_id: str = None, module: str = None, project_name: str = None
) -> dict[str, Any]:
    """
    查询某需求/模块的历史失败率和失败模式。

    失败率 = 该需求/模块的 hallucination 失败次数 / 该需求/模块的总执行次数。
    返回: {"failure_rate": float, "total_runs": int, "failure_patterns": list, "summary": str}
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()

        # 构建过滤条件
        conds = []
        params_total = []
        if project_name:
            conds.append("project = %s")
            params_total.append(project_name)
        if req_id:
            # 精准匹配 case_id（而非子串）
            conds.append("case_id LIKE %s")
            params_total.append("%" + req_id + "%")
        if module:
            conds.append("module LIKE %s")
            params_total.append("%" + module + "%")

        where = (" WHERE " + " AND ".join(conds)) if conds else ""

        # 1) 查询该需求/模块的总执行次数
        sql_total = "SELECT COUNT(*) AS cnt FROM ai_generation_logs" + where
        row_total = conn.execute(sql_total, tuple(params_total)).fetchone()
        total = row_total["cnt"] if row_total else 0
        if total == 0:
            return {
                "failure_rate": 0.0,
                "total_runs": 0,
                "failure_patterns": [],
                "summary": "",
            }

        # 2) 查询该需求/模块的 hallucination 失败次数（同样的条件 + classification=hallucination）
        conds_fail = conds + ["classification = %s"]
        params_fail = params_total + ["hallucination"]
        sql_fail = "SELECT * FROM ai_generation_logs WHERE " + " AND ".join(conds_fail)
        rows = conn.execute(sql_fail, tuple(params_fail)).fetchall()

        if not rows:
            return {
                "failure_rate": 0.0,
                "total_runs": total,
                "failure_patterns": [],
                "summary": "",
            }

        # 提取失败原因
        reasons = []
        for r in rows:
            reason = r.get("reason") or r.get("failure_reason") or ""
            if reason:
                reasons.append(reason)

        patterns = _extract_failure_patterns(reasons)
        failed = len(rows)
        failure_rate = round(failed / max(total, 1), 3)

        # 生成摘要
        if failure_rate > 0.5:
            summary = (
                "⚠️ 历史失败率 %.0f%% (%d/%d)。"
                "高频失败模式：%s。请谨慎生成意图动作，避免上述失败模式。"
            ) % (failure_rate * 100, failed, total, "; ".join(patterns[:3]))
        elif failure_rate > 0.2:
            summary = ("⚠️ 该需求历史失败率 %.0f%%，请关注以下潜在问题：%s") % (
                failure_rate * 100,
                "; ".join(patterns[:2]),
            )
        else:
            summary = ""

        return {
            "failure_rate": failure_rate,
            "total_runs": total,
            "failed_runs": failed,
            "failure_patterns": patterns,
            "summary": summary,
        }
    except Exception as e:
        _log.debug("get_requirement_failure_history 查询失败: %s", str(e)[:60])
        return {
            "failure_rate": 0.0,
            "total_runs": 0,
            "failure_patterns": [],
            "summary": "",
        }
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def _extract_failure_patterns(reasons: list[str]) -> list[str]:
    """从失败原因列表中提取高频模式。"""
    if not reasons:
        return []
    pattern_counts = Counter()
    for r in reasons:
        # 简化：取关键词
        keywords = set()
        for kw in _FAILURE_KEYWORDS:
            if kw.lower() in r.lower():
                keywords.add(kw)
        if not keywords:
            keywords.add("通用失败")
        for kw in keywords:
            pattern_counts[kw] += 1
    sorted_patterns = sorted(pattern_counts.items(), key=lambda x: -x[1])
    return ["%s×%d" % (kw, cnt) for kw, cnt in sorted_patterns[:5]]


def generate_failure_feedback_prompt(
    req_id: str = None, module: str = None, project_name: str = None
) -> str:
    """
    生成可注入 Step2/Step3 Prompt 的历史失败反馈片段。
    Step2: 追加到需求文本后 → "[AI补充]：该需求需明确描述XXX"
    Step3: 追加到 system_prompt → "⚠️ 历史失败模式：..."
    """
    history = get_requirement_failure_history(
        req_id=req_id, module=module, project_name=project_name
    )
    if not history.get("summary"):
        return ""
    return "\n\n## ⚠️ 历史执行反馈（闭环自优化）\n" + history["summary"] + "\n"
