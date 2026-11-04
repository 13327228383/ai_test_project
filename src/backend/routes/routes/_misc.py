# -*- coding: utf-8 -*-
"""
杂项路由
========
- GET /api/stats              — 实时统计数据
- GET /api/figma              — Figma 数据状态（接通真实数据：MySQL 缓存 + 配额）
- GET /api/intent-metrics     — 意图驱动指标（接通 case_store）

【拆分】从 data.py 提取。
【2026-06-23】/api/figma 由 stub 接通真实数据：get_figma_cache_info(纯MySQL) +
  quota_tracker.get_status(纯MySQL) + config 判断(not_configured/need_auth)，
  字段对齐前端 refreshFigma 三层展示，不触发 headless/API。
"""

from flask import jsonify
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

from src.backend.routes._data_helpers import _check_login_and_project


def register(bp):
    """将路由注册到 Blueprint。"""

    @bp.route("/api/stats")
    def api_stats():
        """实时统计数据（需求数、测试用例数、通过/失败/达成率、RAG chunks）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.backend.helpers import _real_stats

            (
                reqs,
                c_total,
                passed,
                failed,
                unexec,
                ungen,
                rate,
                types,
                intent_versions,
                rag_chunks,
                rag_current_version_chunks,
                track_counts,
                type_track_breakdown,
            ) = _real_stats()
            return jsonify(
                {
                    "ok": True,
                    "requirements": reqs,
                    "intents": c_total,
                    "track_counts": track_counts,
                    "type_track_breakdown": type_track_breakdown,
                    "passed": passed,
                    "failed": failed,
                    "unexecuted": unexec,
                    "ungen": ungen,
                    "rate": round(rate, 1),
                    "intent_versions": intent_versions,
                    "rag_chunks": rag_chunks,
                    "rag_current_version_chunks": rag_current_version_chunks,
                    "types": types,
                }
            )
        except Exception as e:
            _log.error("[api_stats] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取统计数据失败: " + str(e)}), 500

    @bp.route("/api/figma")
    def api_figma():
        """★ Figma 数据状态（接通真实数据：读 MySQL 缓存 + 配额，不触发 headless/API）。

        字段对齐前端 refreshFigma 三层判断：
        - not_configured: FIGMA_URL 未配（第1层）
        - need_auth:      FIGMA_KEY(PAT) 未配（第2层）
        - has_cache/is_fresh/design_updated/配额: 第3层正常展示
        数据源: get_figma_cache_info(纯读MySQL) + quota_tracker.get_status(纯读MySQL)。
        """
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )

            from src.core.config_loader import config
            from src.pipeline.steps.step1.figma_cache import get_figma_cache_info
            from src.pipeline.steps.step1.figma_quota import quota_tracker

            figma_url = config.figma_url or ""
            figma_key = config.figma_key or ""
            not_configured = not figma_url
            need_auth = bool(figma_url) and not figma_key

            # 缓存信息（force=True 直读 MySQL，绝不消耗 REST 配额）
            ci = get_figma_cache_info(force=True)
            has_cache = bool(ci.get("has_cache"))

            # 配额状态（纯读 MySQL figma_api_quota 表）
            qs = quota_tracker.get_status()

            return jsonify(
                {
                    "ok": True,
                    "not_configured": not_configured,
                    "using_cache": not_configured and has_cache,  # URL未配但存在旧缓存
                    "need_auth": need_auth,
                    "has_cache": has_cache,
                    "file_name": ci.get("file_name", ""),
                    "fetch_age_hours": ci.get("fetch_age_hours", 0),
                    "node_count": ci.get("node_count", 0),
                    "page_count": ci.get("page_count", 0),
                    "component_count": ci.get("component_count", 0),
                    "size_mb": ci.get("size_mb", 0),
                    "design_updated": ci.get("design_modified", ""),
                    "is_fresh": ci.get("is_synced", True),
                    "total_used": qs.get("total_used") or 0,
                    "monthly_quota": qs.get("monthly_quota") or 0,
                }
            )
        except Exception as e:
            _log.error("[api_figma] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取 Figma 数据失败"}), 500

    @bp.route("/api/intent-metrics")
    def api_case_metrics():
        """★ 意图驱动指标（接通真实数据：聚合 test_cases+case_meta）。

        数据源优先级：case_store（意图驱动完整数据）> case_meta 表（兜底）。
        case_store 无数据时从 case_meta 表直接计算，避免前端显示 "—"。
        """
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            # ★ 防御性同步：确保 config.project_name 与 _current_project() 一致
            from src.backend.state import _current_project
            from src.core.config_loader import config as _cfg

            if _cfg.project_name != _current_project():
                _cfg.reload(_current_project())
                from src.core.excel_handler import excel_handler

                excel_handler.reload_paths()
            from src.pipeline.utils.case_store import case_store

            m = case_store.compute_case_metrics()

            # ★ Fallback：case_store 无数据时，从 case_meta 表直接计算
            #   保证只要有用例元数据，就能看到用例质量指标，不显示 "—"
            if not m.get("has_data") or m.get("total", 0) == 0:
                with guard("api_case_metrics: from src.core.db_manager import get_", log=_log):
                    from src.core.db_manager import get_db
                    from src.core.versioning import current_version_id as _cvid

                    _conn = get_db()
                    _proj = _current_project()
                    if _conn and _proj:
                        _row = _conn.execute(
                            "SELECT "
                            "COUNT(*) as total, "
                            "AVG(case_completeness) as avg_completeness, "
                            "AVG(strictness_score) as avg_strictness, "
                            "AVG(case_driven_rate) as avg_case_driven, "
                            "AVG(evidence_total) as avg_evidence "
                            "FROM case_meta WHERE project=%s AND version_id=%s",
                            (_proj, int(_cvid(_proj) or 0)),
                        ).fetchone()
                        if _row and int(_row["total"] or 0) > 0:
                            _total = int(_row["total"])
                            m = {
                                "has_data": True,
                                "total": _total,
                                "case_driven_rate": round(float(_row["avg_case_driven"] or 0), 1),
                                "strictness_score": round(float(_row["avg_strictness"] or 0), 1),
                                "case_completeness": round(float(_row["avg_completeness"] or 0), 1),
                                "evidence_total": round(float(_row["avg_evidence"] or 0), 1),
                                "_source": "case_meta_fallback",
                            }

            return jsonify(
                {
                    "ok": True,
                    "case_driven_rate": m["case_driven_rate"],
                    "strictness_score": m["strictness_score"],
                    "case_completeness": m["case_completeness"],
                    "evidence_total": m["evidence_total"],
                    "has_data": m["has_data"],
                    "total": m["total"],
                }
            )
        except Exception as e:
            _log.error("[api_case_metrics] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取意图指标失败"}), 500

    def _compute_run_summary_fallback(conn, project, version_id):
        """★ Fallback：run_summary 为空时，从 executions 表实时计算汇总数据。

        通用性：适用于所有项目，仅依赖 executions 表的基础字段。
        目的：避免 Dashboard 质量指标卡片显示空值（"—"），提升演示体验。

        计算逻辑：
          - total_cases / total_passed / total_failed / pass_rate: 从 executions 统计
          - avg_confidence: confidence 字段平均值
          - evidence_coverage: 已执行且有有效置信度的用例比例（近似证据覆盖）
          - evidence_calibrated_confidence: 用 avg_confidence 作为校准置信度近似

        ★ 查询优先级（确保有数据展示）：
          1. 按 version_id 查询（整数 id 字符串形式）
          2. legacy（version_id=0）—— 无活跃版本数据时的显式兜底
          ★ 不做"整个项目"兜底：那会把其他版本的执行数据残留在当前版本。
        """
        try:
            candidates = [("id", str(version_id))]
            if int(version_id or 0) != 0:
                candidates.append(("legacy", "0"))

            row = None
            used_key = None
            # ★ 通用口径修复：avg_confidence 只对真正验证(PASS/FAIL)的用例取均值，
            #   SKIP 未执行 verify，其残余置信度不应稀释平均置信度。
            _CONF_AVG_SQL = (
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN LOWER(status)='pass' THEN 1 ELSE 0 END) as passed, "
                "SUM(CASE WHEN LOWER(status)='fail' THEN 1 ELSE 0 END) as failed, "
                "SUM(CASE WHEN LOWER(status)='skip' THEN 1 ELSE 0 END) as skipped, "
                "AVG(CASE WHEN LOWER(status) IN ('pass','fail') THEN confidence END) as avg_conf, "
                "SUM(CASE WHEN LOWER(status) IN ('pass','fail') AND confidence > 0 THEN 1 ELSE 0 END) as with_conf "
                "FROM executions WHERE project=%s AND version_id=%s"
            )
            for key, vid_candidate in candidates:
                r = conn.execute(
                    _CONF_AVG_SQL,
                    (project, vid_candidate),
                ).fetchone()
                if r and r["total"] > 0:
                    row = r
                    used_key = key
                    break

            if not row or row["total"] == 0:
                return None

            total = int(row["total"] or 0)
            passed = int(row["passed"] or 0)
            failed = int(row["failed"] or 0)
            avg_conf = float(row["avg_conf"] or 0)
            with_conf = int(row["with_conf"] or 0)

            pass_rate = (passed / total) if total > 0 else 0.0
            evidence_coverage = (with_conf / total) if total > 0 else 0.0

            return {
                "total_passed": passed,
                "total_failed": failed,
                "total_cases": total,
                "pass_rate": pass_rate,
                "avg_confidence": avg_conf,
                "evidence_coverage": evidence_coverage,
                "evidence_calibrated_confidence": avg_conf,
                "op_cache_hit_rate": 0.0,
                "capability_hit_rate": 0.0,
                "run_id": "fallback-from-executions",
                "created_at": "",
                "fallback_source": used_key,
            }
        except Exception as fb_e:
            _log.warning("[run-summary-fallback] 计算失败: %s", fb_e)
            return None

    @bp.route("/api/run-summary-latest")
    def api_run_summary_latest():
        """★ 读取当前项目+版本最新一条 run_summary（Step5 写入的权威合计）。

        用于 dashboard 质量指标卡片：证据覆盖率、校验达成率、证据置信度。

        ★ Fallback：run_summary 为空时，从 executions 表实时计算，避免 Dashboard
          显示空值（尤其在只跑了 Step4、没跑 Step5 的场景）。
        """
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.backend.helpers import _current_project
            from src.core.versioning import current_version_id
            from src.core.db_manager import get_connection, release_connection

            project = _current_project()
            vid = str(current_version_id(project))
            conn = None
            try:
                conn = get_connection()
                row = conn.execute(
                    "SELECT total_passed, total_failed, total_cases, pass_rate, "
                    "evidence_coverage, evidence_calibrated_confidence, avg_confidence, "
                    "op_cache_hit_rate, capability_hit_rate, run_id, created_at "
                    "FROM run_summary WHERE project=%s AND version_id=%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project, vid),
                ).fetchone()

                # ★ 根因修复：不再做「版本级查不到 → 回退整个项目最新」的 project-only 兜底。
                #   该兜底会把其他版本的 KPI 数据残留在当前版本（切换版本后出现"老残留"）。
                #   当 vid=0（无活跃版本）时首查已覆盖 legacy 数据；当 vid>0 时只应展示
                #   当前版本数据，查不到即显式返回空，由前端展示"暂无数据（无数据源）"。
                if row:
                    return jsonify(
                        {
                            "ok": True,
                            "data": {
                                "total_passed": int(row["total_passed"] or 0),
                                "total_failed": int(row["total_failed"] or 0),
                                "total_cases": int(row["total_cases"] or 0),
                                "pass_rate": float(row["pass_rate"] or 0),
                                "evidence_coverage": float(row["evidence_coverage"] or 0),
                                "evidence_calibrated_confidence": float(
                                    row["evidence_calibrated_confidence"] or 0
                                ),
                                "avg_confidence": float(row["avg_confidence"] or 0),
                                "op_cache_hit_rate": float(row["op_cache_hit_rate"] or 0),
                                "capability_hit_rate": float(
                                    row["capability_hit_rate"] or 0
                                ),
                                "run_id": row["run_id"],
                                "created_at": str(row["created_at"]),
                            },
                        }
                    )

                # ★ Fallback：run_summary 为空 → 从 executions 表实时计算
                fb = _compute_run_summary_fallback(conn, project, vid)
                if fb:
                    _log.info(
                        "[api_run_summary_latest] run_summary 为空，使用 executions fallback: "
                        "project=%s version=%s cases=%d",
                        project, vid, fb["total_cases"],
                    )
                    return jsonify({"ok": True, "data": fb, "fallback": True})

                return jsonify({"ok": True, "data": None})
            finally:
                if conn:
                    release_connection(conn)
        except Exception as e:
            _log.error("[api_run_summary_latest] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "获取运行摘要失败"}), 500

    @bp.route("/api/step1-status")
    def api_step1_status():
        """读取当前项目+版本最近一次 Step1 数据源状态（sources_status）。"""
        try:
            ok, err = _check_login_and_project()
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            from src.backend.helpers import _current_project
            from src.core.versioning import current_version_label
            from src.core.db_manager import get_connection, release_connection
            import json as _json

            project = _current_project()
            version_label = current_version_label(project)
            conn = None
            try:
                conn = get_connection()
                row = conn.execute(
                    "SELECT content FROM source_documents "
                    "WHERE project=%s AND doc_type='step1_status' AND version_label=%s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (project, version_label),
                ).fetchone()
                if not row:
                    return jsonify({"ok": True, "sources_status": {}})
                content = row.get("content", "{}")
                data = _json.loads(content) if content else {}
                return jsonify({"ok": True, "sources_status": data})
            finally:
                if conn:
                    release_connection(conn)
        except Exception as e:
            _log.error("[api_step1_status] 异常: %s", e, exc_info=True)
            return (
                jsonify({"ok": False, "error": "获取 Step1 状态失败: " + str(e)}),
                500,
            )

    # ★ /api/tool-mode-metrics 已废弃删除：原为写死 0 的 stub，
    #   真实实现在 metrics_page/_fusion.py（经 /api/metrics-summary 的 fusion 字段返回）。
    #   前端 KPI 面板已改读 fusion，消除"同一指标 stub假/fusion真"的缝合怪。
