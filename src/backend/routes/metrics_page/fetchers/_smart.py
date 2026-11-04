# -*- coding: utf-8 -*-
"""
智能能力引擎获取器（业务能力 / 动态 DOM / 区域树 / 模糊匹配）
"""

import logging
from src.core.guarded import guard

from ._utils import _db_conn, _current_project_id, _current_version_id, _num

_log = logging.getLogger(__name__)


def _fetch_smart_capabilities() -> dict:
    """聚合 4 个智能插件的核心指标。"""
    result = {
        "capability": None,
        "dom_sizer": None,
        "region_tree": None,
        "fuzzy_match": None,
        "click_fallback": None,
        "dom_diff": None,
        "summary": {
            "has_data": False,
            "capability_hit_rate": None,
            "fuzzy_hit_rate": None,
            "top_capability_hint": None,
            "avg_limit": None,
            "avg_regions": None,
            "total_plugins_active": 0,
        },
        "note": None,
    }

    conn = _db_conn()
    if conn is None:
        result["note"] = "无法连接数据库"
        return result

    project = _current_project_id()
    if not project:
        result["note"] = "需先选择项目"
        return result

    recent_run_row = None
    with guard("_fetch_smart_capabilities: version_id = _current_version_id()", log=_log, level=logging.WARNING):
        version_id = _current_version_id()
        if version_id:
            recent_run_row = conn.execute(
                """
                SELECT run_id FROM ai_health_metrics
                WHERE project=%s AND version_id=%s
                  AND metric_type IN ('capability', 'dom_sizer', 'region_tree', 'fuzzy_match',
                                      'click_fallback', 'dom_diff')
                ORDER BY id DESC LIMIT 1
                """,
                (project, version_id),
            ).fetchone()
        else:
            recent_run_row = conn.execute(
                """
                SELECT run_id FROM ai_health_metrics
                WHERE project=%s
                  AND metric_type IN ('capability', 'dom_sizer', 'region_tree', 'fuzzy_match',
                                      'click_fallback', 'dom_diff')
                ORDER BY id DESC LIMIT 1
                """,
                (project,),
            ).fetchone()

    if not recent_run_row:
        result["note"] = "尚未运行智能插件（请先执行一次完整流水线）"
        return result

    run_id = recent_run_row["run_id"]
    active_count = 0

    # ★ 根因修复(2026-09-20)：版本隔离。capability 聚合原仅 WHERE run_id=%s，
    #   而历史脏数据(version_id=0)与当前 run 可能共用同一 run_id（如 modsystem__v1_1
    #   被多次执行），导致旧数据混入 —— 出现 cap_miss(152) > cap_total_calls(48) 的矛盾。
    #   现统一追加 AND version_id=%s（active version），只统计当前版本的真数据。
    _vid_filter = version_id or _current_version_id()

    # 1) capability
    try:
        rows = conn.execute(
            """
            SELECT metric_name, category, SUM(value) as total_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s AND version_id=%s AND metric_type='capability'
            GROUP BY metric_name, category
            ORDER BY total_val DESC
            LIMIT 30
            """,
            (project, run_id, _vid_filter),
        ).fetchall()

        cap_stats = {
            "total_calls": 0,
            "hit_count": 0,
            "miss_count": 0,
            "hit_rate": None,
            "avg_score": None,
            "top_hints": [],
        }
        hit_by_hint = {}
        for r in rows:
            m = r["metric_name"] or ""
            cat = r["category"] or ""
            val = _num(r["total_val"], 0) or 0

            # ★ 根因修复(2026-09-20)：统一 capability 口径
            #  - total_calls  = 命中 + 未命中（写端 cap_miss 现已计入 cap_total_calls）
            #  - hit_count    = 命中事件数 = Σ hit_*（cap_hit 存的是单条质量分 float，
            #                   原实现 int(cap_hit) 会截断浮点、且与 hit_* 重复累加 → 虚高）
            #  - miss_count   = Σ cap_miss
            #  - avg_score    = cap_hit 的平均质量分（0~1），与命中次数分离
            if m in ("cap_total_calls", "discovered"):
                cap_stats["total_calls"] = int(val)
            elif m == "cap_miss":
                cap_stats["miss_count"] = int(val)
            elif m == "cap_hit":
                pass  # 质量分单独求均值，不计入命中次数
            elif m.startswith("hit_"):
                _hit = int(val)
                hit_by_hint[m[len("hit_") :]] = (
                    hit_by_hint.get(m[len("hit_") :], 0) + _hit
                )
                cap_stats["hit_count"] += _hit

        # cap_hit 平均质量分（命中事件的综合置信度）
        with guard("_fetch_smart_capabilities: score_row = conn.execute( 'SELECT AV", log=_log):
            score_row = conn.execute(
                "SELECT AVG(value) AS av FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND metric_type='capability' "
                "AND metric_name='cap_hit'",
                (project, run_id),
            ).fetchone()
            if score_row and score_row["av"] is not None:
                cap_stats["avg_score"] = round(_num(score_row["av"], 0.0), 4)

        if cap_stats["total_calls"] > 0:
            cap_stats["hit_rate"] = round(
                cap_stats["hit_count"] / cap_stats["total_calls"] * 100, 2
            )

        cap_stats["top_hints"] = sorted(hit_by_hint.items(), key=lambda x: -x[1])[:8]

        result["capability"] = cap_stats
        if cap_stats["total_calls"] > 0:
            active_count += 1
            result["summary"]["capability_hit_rate"] = cap_stats["hit_rate"]
            if cap_stats["top_hints"]:
                result["summary"]["top_capability_hint"] = cap_stats["top_hints"][0]
    except Exception as e:
        result["capability"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] capability 统计异常: %s", e)

    # 2) dom_sizer
    try:
        sizer = {
            "total_calls": 0,
            "avg_limit": None,
            "compact_count": 0,
            "normal_count": 0,
            "full_count": 0,
            "distribution": {},
        }

        limit_rows = conn.execute(
            """
            SELECT category,
                   AVG(value) as avg_val,
                   COUNT(*) as cnt
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s
              AND metric_type='dom_sizer'
              AND metric_name='limit_value'
            GROUP BY category
            """,
            (project, run_id),
        ).fetchall()

        total_count = 0
        weighted_sum = 0.0
        for r in limit_rows:
            cat = (r["category"] or "").lower()
            avg = _num(r["avg_val"]) or 0
            cnt = _num(r["cnt"], 0) or 0
            total_count += int(cnt)
            weighted_sum += avg * cnt
            if "compact" in cat:
                sizer["compact_count"] += int(cnt)
            elif "normal" in cat:
                sizer["normal_count"] += int(cnt)
            elif "full" in cat:
                sizer["full_count"] += int(cnt)

        if total_count > 0:
            sizer["total_calls"] = total_count
            sizer["avg_limit"] = round(weighted_sum / total_count, 1)

        result["dom_sizer"] = sizer
        if sizer["total_calls"] > 0:
            active_count += 1
            result["summary"]["avg_limit"] = sizer["avg_limit"]
    except Exception as e:
        result["dom_sizer"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] dom_sizer 统计异常: %s", e)

    # 3) region_tree
    try:
        tree = {
            "total_calls": 0,
            "avg_regions": None,
            "avg_elements": None,
            "max_regions": None,
            "dominant_regions": [],
        }

        region_row = conn.execute(
            """
            SELECT AVG(value) as avg_val,
                   MAX(value) as max_val,
                   COUNT(*) as cnt,
                   SUM(value) as total_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s
              AND metric_type='region_tree'
              AND metric_name='nodes_count'
            """,
            (project, run_id),
        ).fetchone()

        if region_row and _num(region_row["cnt"], 0) > 0:
            tree["total_calls"] = int(_num(region_row["cnt"], 0))
            tree["avg_regions"] = round(_num(region_row["avg_val"]) or 0, 1)
            tree["max_regions"] = int(_num(region_row["max_val"]) or 0)
            tree["avg_elements"] = round(_num(region_row["total_val"]) / max(1, _num(region_row["cnt"], 1)), 1)

        elem_row = conn.execute(
            """
            SELECT AVG(value) as avg_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s
              AND metric_type='region_tree'
              AND metric_name='elements_count'
            """,
            (project, run_id),
        ).fetchone()
        if elem_row:
            avg_e = _num(elem_row["avg_val"])
            if avg_e is not None:
                tree["avg_elements"] = round(avg_e, 1)

        region_rows = conn.execute(
            """
            SELECT metric_name, category, SUM(value) as total_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s
              AND metric_type='region_tree'
              AND (metric_name LIKE 'region_%%' OR category LIKE 'region_%%')
            GROUP BY metric_name, category
            ORDER BY total_val DESC
            LIMIT 10
            """,
            (project, run_id),
        ).fetchall()
        for r in region_rows:
            name = r["metric_name"] or ""
            cat = r["category"] or ""
            key = name if name.startswith("region_") else cat
            val = int(_num(r["total_val"], 0) or 0)
            if key:
                tree["dominant_regions"].append((key, val))

        tree["dominant_regions"] = sorted(
            tree["dominant_regions"], key=lambda x: -x[1]
        )[:5]

        result["region_tree"] = tree
        if tree["total_calls"] > 0 or tree["avg_regions"] is not None:
            active_count += 1
            result["summary"]["avg_regions"] = tree["avg_regions"]
    except Exception as e:
        result["region_tree"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] region_tree 统计异常: %s", e)

    # 4) fuzzy_match
    # 对齐写入端(record_fuzzy_match)：metric_type='fuzzy_match'，
    #   metric_name='fuzzy_recall_total'/'fuzzy_recall_hit'/'fuzzy_recall_miss'/
    #   'fuzzy_concept_score'/'fuzzy_char_score'。
    #   （旧口径 'fuzzy' + 'matched_fuzzy'/'avg_fuzzy_score' 已废弃，无需兼容）
    try:
        rows = conn.execute(
            """
            SELECT metric_name,
                   SUM(value) as sum_val,
                   AVG(value) as avg_val,
                   COUNT(*) as cnt
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s AND metric_type='fuzzy_match'
            GROUP BY metric_name
            """,
            (project, run_id),
        ).fetchall()

        fuzzy = {
            "total_calls": 0,
            "hit_count": 0,
            "hit_rate": None,
            "avg_hit_score": None,
        }
        for r in rows:
            m = r["metric_name"] or ""
            s = _num(r["sum_val"], 0) or 0
            avg = _num(r["avg_val"])
            cnt = int(r["cnt"] or 0)
            if m == "fuzzy_recall_total":
                # 每次调用均写 value=1 → SUM 即总调用次数
                fuzzy["total_calls"] = int(s)
            elif m == "fuzzy_recall_hit":
                # 每次命中一行（value=候选数）→ 命中次数=行数 COUNT
                fuzzy["hit_count"] = cnt
            elif m == "fuzzy_concept_score":
                # 语义近义分作为平均命中分的主要质量分
                if avg is not None:
                    fuzzy["avg_hit_score"] = round(avg, 3)

        if fuzzy["total_calls"] > 0:
            fuzzy["hit_rate"] = round(
                fuzzy["hit_count"] / fuzzy["total_calls"] * 100, 2
            )
        result["fuzzy_match"] = fuzzy
        if fuzzy["total_calls"] > 0:
            active_count += 1
            result["summary"]["fuzzy_hit_rate"] = fuzzy["hit_rate"]
    except Exception as e:
        result["fuzzy_match"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] fuzzy_match 统计异常: %s", e)

    # 5) click_fallback（五级回退命中率）
    # DB 实际: direct_hit_rate=100 (直接就是率), level_0_direct=1, level_1_scroll=0...
    try:
        cf_rows = conn.execute(
            """
            SELECT metric_name, SUM(value) as total_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s AND step=4 AND metric_type='click_fallback'
            GROUP BY metric_name
            """,
            (project, run_id),
        ).fetchall()
        cf = {"total": 0, "hits": 0, "hit_rate": None}
        if cf_rows:
            for r in cf_rows:
                m = r["metric_name"] or ""
                v = _num(r["total_val"], 0) or 0
                if m == "direct_hit_rate":
                    # 直接读 DB 存的率（0~100）
                    cf["hit_rate"] = round(v, 2)
                elif m == "level_0_direct":
                    cf["hits"] += int(v)
                elif m.startswith("level_"):
                    cf["total"] += int(v)
            # 如果有分级数据但没直接率，自己算
            if cf["hit_rate"] is None and cf["total"] > 0:
                cf["hit_rate"] = round(cf["hits"] / cf["total"] * 100, 2)
            if cf["total"] == 0:
                cf["total"] = cf["hits"]  # 有 hits 没其他 level，用 hits 当 total
        result["click_fallback"] = cf
    except Exception as e:
        result["click_fallback"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] click_fallback 统计异常: %s", e)

    # 6) dom_diff（DOM变化率）
    # DB 实际: dom_diff.total_diffs (变化总数), dom_diff.auto_fixed (自动修复数)
    try:
        dd_row = conn.execute(
            """
            SELECT metric_name, SUM(value) as total_val
            FROM ai_health_metrics
            WHERE project=%s AND run_id=%s AND step=4 AND metric_type='dom_diff'
            GROUP BY metric_name
            """,
            (project, run_id),
        ).fetchall()
        dd = {"changed": 0, "unchanged": 0, "change_rate": None}
        if dd_row:
            for r in dd_row:
                m = r["metric_name"] or ""
                v = _num(r["total_val"], 0) or 0
                if m == "total_diffs":
                    dd["changed"] = int(v)
                elif m == "auto_fixed":
                    # auto_fixed 也算是 changed 中的已修复部分
                    dd["unchanged"] += int(v)
            total_dd = dd["changed"] + dd["unchanged"]
            if total_dd > 0:
                dd["change_rate"] = round(dd["changed"] / total_dd * 100, 2)
        result["dom_diff"] = dd
    except Exception as e:
        result["dom_diff"] = {"note": f"读取异常: {e}"}
        _log.warning("[metrics] dom_diff 统计异常: %s", e)

    result["summary"]["total_plugins_active"] = active_count
    result["summary"]["has_data"] = active_count > 0

    return result
