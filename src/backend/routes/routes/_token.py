# -*- coding: utf-8 -*-
"""
Token 相关路由
==============
- GET /api/token-stats    — Token 用量统计（当前会话 + 历史累计）
- GET /api/token-dashboard — Token 历史 Dashboard（接通真实数据：browser_ai_round 聚合）

【拆分】从 data.py 提取。
【2026-06-23】token-dashboard 由 stub 接通真实数据：collect_token_dashboard(project)
  从 ai_health_metrics.browser_ai_round 按 (run_id,model) 聚合，费用用 pricing_fetcher
  按模型真实单价估算（per-run/per-model/summary 三级），与 token-stats 同源定价表。
"""

from flask import jsonify
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

from src.backend.routes._data_helpers import _check_login_and_project, _current_project


def collect_token_dashboard(project: str) -> dict:
    """采集 Token 历史 Dashboard 数据（不涉及 HTTP / 权限校验）。

    数据源: ai_health_metrics.browser_ai_round（prompt/completion tokens + elapsed_sec，
            details 含 model）。费用用 pricing_fetcher 按模型真实单价估算。
    无执行数据时 runs=[]/by_model={}/summary 各项 0，前端如实渲染空图表，不伪造。

    返回:
        {ok, runs:[{run_id,input_tokens,output_tokens,total_tokens,cost_estimate,model}],
         by_model:{model:{total_tokens,runs,total_cost}}, models:[...],
         summary:{run_count,total_tokens,total_rounds,avg_elapsed_sec,total_cost}}

    费用口径: cost = (prompt_tokens/1e6)*input_price + (completion_tokens/1e6)*output_price
      （pricing_fetcher.get_price(model)，美元，与 ai/stats.py:calc_cost 同源定价表）。
      browser_ai_round 未存 prompt_cache_hit/miss 拆分（见 health.py:record_browser_ai_round），
      故不计 cache 折扣——这是数据源字段限制，非估算缺失；比 0 准确，与 token-stats
      会话级 total_cost_usd 同口径（会话级亦按 prompt/completion × 单价累计）。
    """
    try:
        from src.models.base import get_conn
        from src.ai.pricing_fetcher import pricing_fetcher

        def _cost_usd(model, prompt_tok, completion_tok):
            """按模型定价估算单组 token 费用（美元）。单价查不到走 _default 兜底。"""
            try:
                p = pricing_fetcher.get_price(model or "") or {}
                inp = float(prompt_tok or 0) / 1_000_000 * float(p.get("input", 0))
                out = float(completion_tok or 0) / 1_000_000 * float(p.get("output", 0))
                return round(inp + out, 6)
            except Exception as e:
                _log.warning("Token费用估算异常: %s", e, exc_info=True)
                return 0.0

        conn = get_conn()
        try:
            # 按 (run_id, model) 聚合 tokens —— 一次查询同时支撑 per-run 与 per-model
            rm_rows = conn.execute(
                "SELECT run_id, "
                "JSON_UNQUOTE(JSON_EXTRACT(details,'$.model')) AS model, "
                "SUM(CASE WHEN metric_name='prompt_tokens' THEN value ELSE 0 END) AS input_tokens, "
                "SUM(CASE WHEN metric_name='completion_tokens' THEN value ELSE 0 END) AS output_tokens, "
                "MAX(created_at) AS last_at "
                "FROM ai_health_metrics "
                "WHERE project=%s AND metric_type='browser_ai_round' "
                "AND metric_name IN ('prompt_tokens','completion_tokens') "
                "GROUP BY run_id, model",
                (project,),
            ).fetchall()

            # 单遍循环：同时建 run 聚合与 model 聚合（_cost_usd 每行只调一次）
            run_map = (
                {}
            )  # run_id -> {input, output, total, cost, models:{model:total}, last_at}
            model_agg = {}  # model  -> {total_tokens, runs:set, total_cost}
            for r in rm_rows:
                rid = r["run_id"]
                inp = int(r["input_tokens"] or 0)
                out = int(r["output_tokens"] or 0)
                tt = inp + out
                m = r["model"] or "未知"
                cost = _cost_usd(m, inp, out)

                rs = run_map.setdefault(
                    rid,
                    {
                        "input": 0,
                        "output": 0,
                        "total": 0,
                        "cost": 0.0,
                        "models": {},
                        "last_at": r["last_at"],
                    },
                )
                rs["input"] += inp
                rs["output"] += out
                rs["total"] += tt
                rs["cost"] += cost
                rs["models"][m] = rs["models"].get(m, 0) + tt
                if r["last_at"] and (not rs["last_at"] or r["last_at"] > rs["last_at"]):
                    rs["last_at"] = r["last_at"]

                ms = model_agg.setdefault(
                    m, {"total_tokens": 0, "runs": set(), "total_cost": 0.0}
                )
                ms["total_tokens"] += tt
                ms["runs"].add(rid)
                ms["total_cost"] += cost

            # 最近 20 个 run（按最后活动时间倒序）
            sorted_runs = sorted(
                run_map.items(), key=lambda kv: kv[1]["last_at"] or "", reverse=True
            )[:20]
            runs = []
            for rid, s in sorted_runs:
                rep_model = max(s["models"], key=s["models"].get) if s["models"] else ""
                runs.append(
                    {
                        "run_id": rid,
                        "input_tokens": s["input"],
                        "output_tokens": s["output"],
                        "total_tokens": s["total"],
                        "cost_estimate": round(s["cost"], 6),
                        "model": rep_model,
                    }
                )

            by_model = {
                m: {
                    "total_tokens": v["total_tokens"],
                    "runs": len(v["runs"]),
                    "total_cost": round(v["total_cost"], 6),
                }
                for m, v in model_agg.items()
            }

            # 总轮次 + 平均耗时（每轮一条 elapsed_sec）
            agg = conn.execute(
                "SELECT "
                "SUM(CASE WHEN metric_name='elapsed_sec' THEN value ELSE 0 END) AS total_elapsed, "
                "SUM(CASE WHEN metric_name='elapsed_sec' THEN 1 ELSE 0 END) AS total_rounds, "
                "COUNT(DISTINCT run_id) AS run_count "
                "FROM ai_health_metrics "
                "WHERE project=%s AND metric_type='browser_ai_round'",
                (project,),
            ).fetchone()
            total_rounds = int(agg["total_rounds"] or 0) if agg else 0
            run_count = int(agg["run_count"] or 0) if agg else 0
            avg_elapsed_sec = (
                round(float(agg["total_elapsed"] or 0) / max(total_rounds, 1), 1)
                if agg and total_rounds > 0
                else 0
            )

            return {
                "ok": True,
                "runs": runs,
                "by_model": by_model,
                "models": list(by_model.keys()),
                "summary": {
                    "run_count": run_count,
                    "total_tokens": sum(s["total"] for s in run_map.values()),
                    "total_rounds": total_rounds,
                    "avg_elapsed_sec": avg_elapsed_sec,
                    "total_cost": round(sum(s["cost"] for s in run_map.values()), 6),
                },
            }
        finally:
            if conn:
                with guard("collect_token_dashboard: conn.close()", log=_log):
                    conn.close()
    except Exception as e:
        _log.error("[collect_token_dashboard] 异常: %s", e, exc_info=True)
        return {"ok": False, "error": "服务器内部错误"}


# ★ v6: Phase 级 Token 用量映射表
_PHASE_PATTERNS = [
    # (phase_label, caller_name_patterns)
    ("Step1-拉取", ["数据源", "拉取", "fetch", "yapi", "confluence", "figma"]),
    ("Step2-P1-发现", ["模块发现", "全局文档理解"]),
    ("Step2-P2-拆解", ["需求拆解"]),
    ("Step2-P3-合并", ["语义聚类", "矛盾仲裁"]),
    ("Step2-P4-红队", ["红队-源证据", "红队-一致性", "红队-完备性"]),
    ("Step2-P5-仲裁", ["AI仲裁-需求"]),
    ("Step3-建模", ["测试用例生成", "校验"]),
    ("Step3-蓝队", ["蓝队-"]),
    ("Step3-红队", ["红队-需求覆盖", "红队-可执行性", "红队-场景完备"]),
    ("Step3-仲裁", ["AI仲裁-意图"]),
    ("Step4-执行", ["BrowserAi", "browser", "意图执行", "visual"]),
    ("Step5-报告", ["报告"]),
    ("其他", []),  # fallback
]


def _classify_phase(caller_name: str) -> str:
    """根据 caller_name 归类到 Phase。"""
    if not caller_name:
        return "其他"
    for phase, patterns in _PHASE_PATTERNS:
        if not patterns:
            continue
        for pat in patterns:
            if pat.lower() in caller_name.lower():
                return phase
    return "其他"


def collect_token_phase_breakdown(project: str) -> dict:
    """★ v6: Phase 级 Token 用量明细。

    从 ai_health_metrics 读取 token_usage 记录，按 details.caller_name 归类到
    Step/Phase，返回各 Phase 的 prompt/completion/cost 合计。

    返回: {ok, phases: [{phase, prompt_tokens, completion_tokens, total_tokens, cost_usd, calls}]}
    """
    try:
        from src.models.base import get_conn
        from src.ai.pricing_fetcher import pricing_fetcher

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT metric_name, SUM(value) as total_value, "
                "COUNT(*) as call_count, "
                "details "
                "FROM ai_health_metrics "
                "WHERE project=%s AND metric_type='token_usage' "
                "AND metric_name IN ('prompt_tokens','completion_tokens','cost_usd') "
                "AND details IS NOT NULL AND details != '' "
                "GROUP BY metric_name, details",
                (project,),
            ).fetchall()
        finally:
            if conn:
                with guard("collect_token_phase_breakdown: conn.close()", log=_log):
                    conn.close()

        if not rows:
            return {
                "ok": True,
                "phases": [],
                "note": "暂无 Phase 级 Token 数据（需执行流水线后才有 caller_name 明细）",
            }

        # 按 phase 聚合
        import json as _json

        phase_data: dict = (
            {}
        )  # phase -> {prompt_tokens, completion_tokens, cost_usd, calls:set}

        for r in rows:
            metric = r["metric_name"]
            value = float(r["total_value"] or 0)
            details_str = r.get("details", "") or ""
            try:
                details = _json.loads(details_str) if details_str else {}
            except Exception:
                _log.warning("解析details JSON失败", exc_info=True)
                details = {}
            caller_name = details.get("caller_name", "")
            model = details.get("model", "")
            phase = _classify_phase(caller_name)

            pd = phase_data.setdefault(
                phase,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                    "calls": set(),
                },
            )
            if metric == "prompt_tokens":
                pd["prompt_tokens"] += int(value)
            elif metric == "completion_tokens":
                pd["completion_tokens"] += int(value)
            elif metric == "cost_usd":
                pd["cost_usd"] += round(value, 6)
            pd["calls"].add(caller_name or "unknown")

        # 排序：按 phase 名称
        phases = []
        for phase_name in [p[0] for p in _PHASE_PATTERNS if p[1]] + ["其他"]:
            if phase_name not in phase_data:
                continue
            pd = phase_data[phase_name]
            phases.append(
                {
                    "phase": phase_name,
                    "prompt_tokens": pd["prompt_tokens"],
                    "completion_tokens": pd["completion_tokens"],
                    "total_tokens": pd["prompt_tokens"] + pd["completion_tokens"],
                    "cost_usd": pd["cost_usd"],
                    "calls": len(pd["calls"]),
                }
            )

        return {"ok": True, "phases": phases}
    except Exception as e:
        _log.error("[collect_token_phase_breakdown] 异常: %s", e, exc_info=True)
        return {"ok": False, "error": "服务器内部错误"}


def register(bp):
    """将路由注册到 Blueprint。"""

    @bp.route("/api/token-stats")
    def api_token_stats():
        """★ Token 用量统计（Step 2 拆解 + Step 3 建模）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )

            from src.ai.ai_client import ai_brain

            stats = ai_brain.get_stats()

            # ★ 读取进程内存累计值（observer 回调实时更新，本次会话的核心数据源）
            from src.backend import state as _st

            try:
                mem = _st._read_token_stats()
                mem_input = mem.get("total_input_tokens", 0)
                mem_output = mem.get("total_output_tokens", 0)
                mem_time = mem.get("total_ai_time", 0)
                mem_cost = mem.get("total_ai_cost", 0)
            except Exception:
                _log.warning("读取内存token统计失败", exc_info=True)
                mem_input = mem_output = mem_time = mem_cost = 0

            # ★ 读取 DB 历史基线（进程启动时快照，延迟加载一次后缓存）
            #    observer 每笔调用写 delta 到 DB，但基线在启动时已快照，
            #    不会重复计数当前会话数据。
            baseline = _st._load_db_token_baseline()
            history = {
                "total_calls": 0,
                "total_prompt_tokens": baseline.get("input_tokens", 0),
                "total_completion_tokens": baseline.get("output_tokens", 0),
                "total_cost_usd": baseline.get("cost_usd", 0),
            }

            # ★ 合计 = 内存累计(本次会话) + DB基线(历史会话，启动时快照)
            #    启动时 DB 不含当前会话的 delta，故无重复计数。
            _usd_to_cny = lambda v: round(v * 7.2, 6) if v else 0
            total_input = mem_input + history["total_prompt_tokens"]
            total_output = mem_output + history["total_completion_tokens"]
            total_cost_usd = mem_cost + history["total_cost_usd"]
            result = {
                "ok": True,
                "current_session": stats,
                "history": history,
                "pricing": stats.get("pricing", {}),
                "cur": {
                    "input_tokens": stats.get("total_prompt_tokens", 0),
                    "output_tokens": stats.get("total_completion_tokens", 0),
                    "cost_cny": _usd_to_cny(stats.get("total_cost_usd", 0)),
                    "time": 0,
                    "dom_count": 0,
                },
                "total": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cost_cny": _usd_to_cny(total_cost_usd),
                    "time": int(mem_time),
                    "dom_original": 0,
                },
                "switch_reason": "",
                "model": stats.get("model", ""),
            }
            return jsonify(result)
        except Exception as e:
            _log.error("[api_token_stats] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取 Token 统计失败"}), 500

    @bp.route("/api/token-dashboard")
    def api_token_dashboard():
        """★ Token 历史 Dashboard（接通真实数据：browser_ai_round 聚合）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            project = _current_project()
            if not project:
                return jsonify({"ok": False, "error": "请先选择项目"}), 400
            data = collect_token_dashboard(project)
            if not data.get("ok"):
                return jsonify(data), 500
            return jsonify(data)
        except Exception as e:
            _log.error("[api_token_dashboard] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取 Token Dashboard 失败"}), 500

    @bp.route("/api/token-phase-breakdown")
    def api_token_phase_breakdown():
        """★ v6: Phase 级 Token 用量明细（按 caller_name 归类 Step/Phase）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            project = _current_project()
            if not project:
                return jsonify({"ok": False, "error": "请先选择项目"}), 400
            data = collect_token_phase_breakdown(project)
            if not data.get("ok"):
                return jsonify(data), 500
            return jsonify(data)
        except Exception as e:
            _log.error("[api_token_phase_breakdown] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取 Phase Token 明细失败"}), 500
