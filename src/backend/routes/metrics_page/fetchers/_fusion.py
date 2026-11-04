# -*- coding: utf-8 -*-
"""
融合架构获取器（Tool-mode / Recipe 缓存 / 原子回滚）
"""

import logging

from ._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    _num,
    latest_run_id_for_metrics,
)

_log = logging.getLogger(__name__)


def _fetch_fusion(conn=None, run_id=None) -> dict:
    """融合架构指标。

    ★ 2026-07-13: 不再使用全局共享 run_id，改为按本 fetcher 关心的 metric_type
    集合查找最近 run。run_id 参数已废弃（保留签名兼容）。
    """
    result = {
        "tool_mode_rate": None,
        "recipe_hit_rate": None,
        "rollback_count": None,
        "avg_tool_ms": None,
        "ai_token_total": None,
        "ai_loop_usage_rate": None,
        "ai_loop_pass_rate": None,
        "note": None,
    }

    project = _current_project_id()
    if not project:
        result["note"] = "需先选择项目"
        return result

    _owns_conn = conn is None
    if _owns_conn:
        conn = _db_conn()
    if conn is None:
        result["note"] = "无法连接数据库"
        return result

    try:
        # ★ 按本 fetcher 关心的 metric_type 查找最近 run
        version_id = _current_version_id()
        run_id = latest_run_id_for_metrics(
            conn,
            project,
            version_id,
            ["tool_mode", "op_cache", "atomic_rollback", "browser_ai_round", "ai_loop"],
        )
        if not run_id:
            result["note"] = "暂无执行数据"
            return result

        # --- Tool-mode 使用率 ---
        # ★ 对齐写入端(record_tool_mode)：DB 实际存 tool_mode.tool_mode_calls + tool_mode.ai_total_calls
        #   (旧字段 browser_mode 为 0/1 标志，保留兼容)。rate = tool_mode_calls / ai_total_calls * 100
        tm_rows = conn.execute(
            "SELECT metric_name, SUM(value) as v FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='tool_mode' GROUP BY metric_name",
            (project, run_id),
        ).fetchall()
        _tm = {r["metric_name"]: _num(r["v"], 0) for r in tm_rows}
        _tool_mode = _tm.get("tool_mode_calls")
        _ai_total = _tm.get("ai_total_calls")
        if _tool_mode is not None and (_ai_total or 0) > 0:
            result["tool_mode_rate"] = round(_tool_mode / _ai_total * 100, 2)
        elif _tool_mode is not None:
            result["tool_mode_rate"] = round(_tool_mode * 100, 2)

        # --- Recipe 缓存 ---
        # DB 实际存: op_cache.hit_rate (写入端持久化的 rate)
        # 根因：写入端 Step4 capability_cache 持久化没实现时，hit_rate 永远是 0
        # 当 DB 值全 0 时 → 返回 None + note（避免误导性 0%）
        cache_rows = conn.execute(
            "SELECT metric_name, SUM(value) as total FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='op_cache' GROUP BY metric_name",
            (project, run_id),
        ).fetchall()
        if cache_rows:
            direct_rate = sum(
                _num(r["total"], 0)
                for r in cache_rows
                if r["metric_name"] == "hit_rate"
            )
            hits = sum(
                _num(r["total"], 0)
                for r in cache_rows
                if r["metric_name"] == "cache_hit"
            )
            misses = sum(
                _num(r["total"], 0)
                for r in cache_rows
                if r["metric_name"] == "cache_miss"
            )
            total = hits + misses

            if direct_rate > 0:
                result["recipe_hit_rate"] = round(direct_rate, 2)
            elif total > 0:
                result["recipe_hit_rate"] = round(hits / total * 100, 2)
            else:
                # DB 全 0 → 尝试查 capability_cache 表（写入端可能绕过 ai_health_metrics）
                try:
                    cc_row = conn.execute(
                        "SELECT COUNT(*) as n FROM capability_cache WHERE project=%s",
                        (project,),
                    ).fetchone()
                    if cc_row and cc_row["n"] > 0:
                        hit_total = conn.execute(
                            "SELECT COALESCE(SUM(hit_count),0) as h, "
                            "COALESCE(SUM(success_count),0) as s "
                            "FROM capability_cache WHERE project=%s",
                            (project,),
                        ).fetchone()
                        h = int(hit_total["h"] or 0)
                        s = int(hit_total["s"] or 0)
                        denom = h + s
                        if denom > 0:
                            result["recipe_hit_rate"] = round(h / denom * 100, 2)
                        else:
                            result["recipe_hit_rate"] = None
                            result["_recipe_note"] = "缓存事件未持久化（capability_cache 无 hit/miss 记录）"
                    else:
                        result["recipe_hit_rate"] = None
                        result["_recipe_note"] = "能力缓存表为空（写入端未启用）"
                except Exception as _e:
                    result["recipe_hit_rate"] = None

        # --- 原子回滚 ---
        # ★ 对齐写入端(record_atomic_rollback)：DB 实际存 atomic_rollback.atomic_rollback
        #   (保留旧名 total 兼容)
        rb_row = conn.execute(
            "SELECT SUM(value) as val FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='atomic_rollback' "
            "AND metric_name IN ('atomic_rollback','total')",
            (project, run_id),
        ).fetchone()
        if rb_row and _num(rb_row["val"], 0) > 0:
            result["rollback_count"] = _num(rb_row["val"], 0)

        # --- 工具调用平均耗时 ---
        latency_row = conn.execute(
            "SELECT metric_name, SUM(value) as total_val, COUNT(*) as cnt "
            "FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 AND metric_type='browser_ai_round' "
            "AND metric_name='elapsed_sec' GROUP BY metric_name",
            (project, run_id),
        ).fetchone()
        if latency_row and _num(latency_row["total_val"], 0) > 0:
            result["avg_tool_ms"] = round(
                _num(latency_row["total_val"], 0)
                / max(1, _num(latency_row["cnt"], 1))
                * 1000,
                1,
            )

        # --- AI Token 消耗（prompt + completion） ---
        token_row = conn.execute(
            "SELECT metric_name, SUM(value) as total_val FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='browser_ai_round' "
            "AND metric_name IN ('prompt_tokens','completion_tokens') GROUP BY metric_name",
            (project, run_id),
        ).fetchall()
        if token_row:
            prompt_total = sum(
                _num(r["total_val"], 0)
                for r in token_row
                if r["metric_name"] == "prompt_tokens"
            )
            completion_total = sum(
                _num(r["total_val"], 0)
                for r in token_row
                if r["metric_name"] == "completion_tokens"
            )
            result["ai_token_total"] = prompt_total + completion_total
            result["ai_token_prompt"] = prompt_total
            result["ai_token_completion"] = completion_total

        # --- AI loop 使用率 & 兜底成功率 ---
        # 对齐写入端(record_ai_loop_entry / record_ai_loop_pass)：
        #   metric_name='entered' = 智能兜底进入数；'passed' = AI决策使用例通过数。
        #   （旧名 total_loops/avg_iterations 兼容保留）
        # 公式:
        #   使用率 = entered / (cache_hit + entered)
        #   兜底成功率 = passed / entered
        ai_loop_rows = conn.execute(
            "SELECT metric_name, SUM(value) as total FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='ai_loop' GROUP BY metric_name",
            (project, run_id),
        ).fetchall()
        entered = 0
        passed = 0
        if ai_loop_rows:
            for r in ai_loop_rows:
                m = r["metric_name"] or ""
                v = _num(r["total"], 0)
                if m in ("entered", "total_loops"):
                    entered += v
                elif m == "passed":
                    passed += v
        # 分母 = 查表命中率（从 cache_rows 的 hit_rate 或 cache_hit）+ AI循环进入
        cache_hit_abs = 0
        if cache_rows:
            for r in cache_rows:
                if r["metric_name"] == "cache_hit":
                    cache_hit_abs = _num(r["total"], 0)
        loop_total = cache_hit_abs + entered
        if loop_total > 0 and entered > 0:
            result["ai_loop_usage_rate"] = round(entered / loop_total * 100, 2)
        if entered > 0:
            result["ai_loop_pass_rate"] = round(passed / entered * 100, 2)

        result["_source_note"] = "数据来源: ai_health_metrics 表"

    except Exception as e:
        _log.warning("[metrics] _fetch_fusion 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("health + metric + ai: %s", _e, exc_info=True)

    return result
