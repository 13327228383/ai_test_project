# -*- coding: utf-8 -*-
"""
Step2 功能清单驱动拆解指标获取器
================================
读取 ai_health_metrics 表中 metric_type='step2_breakdown' 的最新 run_id 数据，
返回 v3 功能清单驱动拆解的 KPI 指标（供前端指标中心渲染）。

指标清单（与 record_step2_breakdown_stats 写入对齐）：
- step2_feature_count:      目标系统功能总数
- step2_matched_count:      匹配到需求文档的功能数
- step2_undocumented_count: 未文档化功能数
- step2_ai_calls:           AI 提取三元组的调用次数
- step2_total_reqs:         最终生成的需求条目数
- match_stats:              三层匹配统计（keyword/embed/undocumented）— 从 details JSON 提取
- verify_stats:             原文核对统计（passed/failed/empty/skipped_implicit）— 从 details JSON 提取
- coverage_stats:           逆向覆盖统计（covered/uncovered）— 从 details JSON 提取
- ★ step2_trust_l1:         L1 显式需求数（#9/#11 三级信任模型）
- ★ step2_trust_l2:         L2 通用隐性需求数（#10/#11 模式库+AI推断）
- ★ step2_trust_l3:         L3 业务隐性候选数（#11 未文档化未匹配模式库）
- ★ implicit_stats:         模式库补全统计（l2_matched/l3_unmatched/pattern_count）— 从 details JSON 提取
- ★ multi_source_stats:     多源功能提取统计（dom/figma/requirement/yapi）— 从 details JSON 提取
"""

import json
import logging

from ._utils import _db_conn, _current_project_id, _current_version_id, _num

_log = logging.getLogger(__name__)


def _fetch_step2_breakdown(conn=None) -> dict:
    """读取最近一次 run 的 Step2 功能清单驱动拆解指标。

    可选传入共享 conn（run_id 查询条件按 metric_type='step2_breakdown' 自查，
    与通用 run_id 不同，故不接收 run_id 参数）。
    """
    result = {
        "feature_count": None,
        "matched_count": None,
        "undocumented_count": None,
        "ai_calls": None,
        "total_reqs": None,
        "match_stats": None,
        "verify_stats": None,
        "coverage_stats": None,
        # ★ #14 三级信任分布
        "trust_l1_explicit": None,
        "trust_l2_implicit_common": None,
        "trust_l3_implicit_business": None,
        # ★ #14 模式库补全统计
        "implicit_stats": None,
        # ★ #14 多源功能提取统计
        "multi_source_stats": None,
        # 沉睡指标：双向覆盖率 + 置信度分布 + 规则/AI覆盖率
        "coverage": None,
        "confidence": None,
        "rule_coverage_rate": None,
        "ai_coverage_rate": None,
        "note": None,
    }

    project = _current_project_id()
    if not project:
        result["note"] = "需先选择项目"
        return result

    version_id = _current_version_id()

    _owns_conn = conn is None
    if _owns_conn:
        conn = _db_conn()
    if conn is None:
        result["note"] = "无法连接数据库"
        return result

    try:
        # ★ 查询优先级：版本级 → 项目级（演示友好，避免因版本号不匹配导致全空）
        run_id = None
        used_scope = None
        candidates = []
        if version_id:
            candidates.append(("version", version_id))
        candidates.append(("project", None))

        for scope, vid_candidate in candidates:
            _ver_filter = "AND version_id=%s" if vid_candidate is not None else ""
            _ver_params = (vid_candidate,) if vid_candidate is not None else ()
            run_row = conn.execute(
                "SELECT run_id FROM ai_health_metrics "
                f"WHERE project=%s AND metric_type='step2_breakdown' {_ver_filter} "
                "ORDER BY created_at DESC LIMIT 1",
                (project,) + _ver_params,
            ).fetchone()
            if run_row:
                run_id = run_row["run_id"]
                used_scope = scope
                break

        if not run_id:
            result["note"] = "暂无 Step2 拆解数据"
            return result

        # 读取该 run 的所有 step2_breakdown 指标（run_id 唯一，无需再加 version 过滤）
        rows = (
            conn.execute(
                "SELECT metric_name, value, total, details "
                "FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND metric_type='step2_breakdown'",
                (project, run_id),
            ).fetchall()
            or []
        )

        if not rows:
            result["note"] = "Step2 拆解指标未写入"
            return result

        # 从 details JSON 提取嵌套统计（所有行 details 相同，取第一行）
        details_json = rows[0].get("details", "") or "{}"
        try:
            details = (
                json.loads(details_json)
                if isinstance(details_json, str)
                else (details_json or {})
            )
        except (json.JSONDecodeError, TypeError):
            details = {}

        # 标量指标：从各行 value 读取
        scalar_map = {
            "step2_feature_count": "feature_count",
            "step2_matched_count": "matched_count",
            "step2_undocumented_count": "undocumented_count",
            "step2_ai_calls": "ai_calls",
            "step2_total_reqs": "total_reqs",
            # ★ #14 三级信任分布独立 metric_name
            "step2_trust_l1": "trust_l1_explicit",
            "step2_trust_l2": "trust_l2_implicit_common",
            "step2_trust_l3": "trust_l3_implicit_business",
        }
        for row in rows:
            metric_name = row.get("metric_name", "")
            if metric_name in scalar_map:
                result[scalar_map[metric_name]] = _num(row.get("value"), 0)

        # 嵌套统计：从 details JSON 读取
        result["match_stats"] = details.get("match_stats", {})
        result["verify_stats"] = details.get("verify_stats", {})
        result["coverage_stats"] = details.get("coverage_stats", {})
        # ★ #14 模式库补全统计 + 多源功能提取统计
        result["implicit_stats"] = details.get("implicit_stats", {})
        result["multi_source_stats"] = details.get("multi_source_stats", {})

        # --- coverage（双向覆盖率，沉睡指标，同 run_id 下 metric_type='coverage'）---
        cov_forward = None
        cov_orphan = None
        cov_uncovered = None
        for crow in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=2 AND metric_type='coverage'",
            (project, run_id),
        ).fetchall():
            mn = crow["metric_name"]
            # 兼容历史命名：forward_coverage_rate (早期) / coverage_forward (当前引擎产出)
            if mn in ("forward_coverage_rate", "coverage_forward"):
                cov_forward = _num(crow["value"], 0)
            elif mn == "orphan_modules":
                cov_orphan = _num(crow["value"], 0)
            elif mn == "uncovered_sections":
                cov_uncovered = _num(crow["value"], 0)
        result["coverage"] = {
            "forward_rate": cov_forward,
            "orphan_modules": cov_orphan,
            "uncovered_sections": cov_uncovered,
        }

        # --- step2_confidence（置信度分布，沉睡指标，同 run_id 下 metric_type='step2_confidence'）---
        conf_mean = None
        conf_high = None
        conf_medium = None
        conf_low = None
        for crow in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=2 AND metric_type='step2_confidence'",
            (project, run_id),
        ).fetchall():
            mn = crow["metric_name"]
            if mn == "step2_confidence_mean":
                conf_mean = _num(crow["value"], 0)
            elif mn == "step2_confidence_high":
                conf_high = _num(crow["value"], 0)
            elif mn == "step2_confidence_medium":
                conf_medium = _num(crow["value"], 0)
            elif mn == "step2_confidence_low":
                conf_low = _num(crow["value"], 0)
        result["confidence"] = {
            "mean": conf_mean,
            "high": conf_high,
            "medium": conf_medium,
            "low": conf_low,
        }

        # --- 规则覆盖率 vs AI 覆盖率（从 step2_rule_count + step2_ai_calls 计算）---
        rule_count = None
        ai_count = result.get("ai_calls")
        for row in rows:
            if row.get("metric_name") == "step2_rule_count":
                rule_count = _num(row.get("value"), 0)
                break
        if rule_count is not None and ai_count is not None:
            total_ops = rule_count + ai_count
            if total_ops > 0:
                result["rule_coverage_rate"] = round(rule_count / total_ops * 100, 1)
                result["ai_coverage_rate"] = round(ai_count / total_ops * 100, 1)

    except Exception as e:
        _log.warning("[metrics] _fetch_step2_breakdown 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("step2_breakdown fetcher conn close: %s", _e, exc_info=True)

    return result
