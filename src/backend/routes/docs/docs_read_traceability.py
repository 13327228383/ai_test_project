# -*- coding: utf-8 -*-
"""
文档中心 — 追溯链路 + 变更对比 + RAG 面板路由
=============================================
"""

from flask import jsonify
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


@docs_bp.route("/api/docs/traceability")
def _api_traceability():
    """获取追溯链路数据。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.traceability_model import traceability_engine

        links = traceability_engine._load_cache()
        if not links:
            return jsonify(
                {"links": {}, "count": 0, "message": "无追溯链路数据，请先运行 Step2"}
            )
        serialized = {}
        for rid, link in links.items():
            if hasattr(link, "__dataclass_fields__"):
                serialized[rid] = {
                    "req_id": link.req_id,
                    "module": link.module,
                    "api_endpoints": link.api_endpoints,
                    "db_tables": (
                        list(link.db_tables.keys())
                        if isinstance(link.db_tables, dict)
                        else link.db_tables
                    ),
                    "figma_pages": link.figma_pages,
                    "confidence": link.confidence,
                    "feature_id": link.feature_id,
                }
            else:
                serialized[rid] = link
        return jsonify({"links": serialized, "count": len(serialized)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/traceability/coverage")
def _api_traceability_coverage():
    """获取增强覆盖率报告（含数据源维度）。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.traceability_model import traceability_engine
        from src.core.excel_handler import excel_handler

        links = traceability_engine._load_cache()
        intents = excel_handler.read_test_cases()
        if not links:
            return jsonify({"ok": False, "error": "无追溯链路数据"}), 404
        report = traceability_engine.compute_all_coverage(links, intents)
        return jsonify(
            {
                "summary": report.summary,
                "api_coverage": {
                    k: v for k, v in list(report.api_coverage.items())[:30]
                },
                "db_coverage": {k: v for k, v in list(report.db_coverage.items())[:30]},
                "page_coverage": {
                    k: v for k, v in list(report.page_coverage.items())[:30]
                },
                "trace_completeness": {
                    k: v for k, v in list(report.trace_completeness.items())[:30]
                },
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/traceability/matrix")
def _api_traceability_matrix():
    """获取追溯矩阵（所有需求的 API/DB/Page 链路）。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.traceability_model import traceability_engine

        links = traceability_engine._load_cache()
        if not links:
            return jsonify({"matrix": [], "count": 0})
        matrix = []
        for rid, link in sorted(links.items()):
            if hasattr(link, "__dataclass_fields__"):
                matrix.append(
                    {
                        "req_id": link.req_id,
                        "module": link.module,
                        "api_count": len(link.api_endpoints),
                        "db_count": len(link.db_tables),
                        "page_count": len(link.figma_pages),
                        "confidence": link.confidence,
                    }
                )
            elif isinstance(link, dict):
                matrix.append(
                    {
                        "req_id": link.get("req_id", rid),
                        "module": link.get("module", ""),
                        "api_count": len(link.get("api_endpoints", [])),
                        "db_count": len(link.get("db_tables", {})),
                        "page_count": len(link.get("figma_pages", [])),
                        "confidence": link.get("confidence", "medium"),
                    }
                )
        return jsonify({"matrix": matrix, "count": len(matrix)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/tool-execution-log")
def _api_tool_execution_log():
    """查询执行指令日志（tool_execution_log 表）。

    支持参数：
    - case_id: 按用例ID过滤（精确匹配）
    - trace_id: 按链路追踪ID过滤（精确匹配）
    - limit: 返回条数上限（默认 100，最大 500）
    - offset: 偏移量（默认 0）

    返回：按 created_at 倒序的 tool_call 执行详情列表。
    """
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from flask import request

    try:
        from src.core.config_loader import config

        project = config.project_name or ""
        case_id = (request.args.get("case_id") or "").strip()
        trace_id = (request.args.get("trace_id") or "").strip()
        try:
            limit = max(1, min(500, int(request.args.get("limit", 100))))
        except (ValueError, TypeError):
            limit = 100
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except (ValueError, TypeError):
            offset = 0

        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            sql = (
                "SELECT id, case_id, step_desc, trace_id, tool_name, "
                "tool_args_json, result_ok, result_content, elapsed_ms, "
                "atomic_rollback, tool_mode, model, created_at "
                "FROM tool_execution_log WHERE project=%s"
            )
            args = [project]
            if case_id:
                sql += " AND case_id=%s"
                args.append(case_id)
            if trace_id:
                sql += " AND trace_id=%s"
                args.append(trace_id)
            sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            args.extend([limit, offset])
            rows = conn.execute(sql, tuple(args)).fetchall() or []

            # 统计总数（用于分页）
            count_sql = (
                "SELECT COUNT(*) AS cnt FROM tool_execution_log WHERE project=%s"
            )
            count_args = [project]
            if case_id:
                count_sql += " AND case_id=%s"
                count_args.append(case_id)
            if trace_id:
                count_sql += " AND trace_id=%s"
                count_args.append(trace_id)
            cnt_row = conn.execute(count_sql, tuple(count_args)).fetchone()
            total = int(
                (
                    cnt_row["cnt"]
                    if isinstance(cnt_row, dict)
                    else (cnt_row[0] if cnt_row else 0)
                )
                or 0
            )

            import json as _json

            items = []
            for row in rows:
                item = dict(row)
                # 解析 tool_args_json
                taj = item.get("tool_args_json")
                if isinstance(taj, str):
                    try:
                        item["tool_args_json"] = _json.loads(taj) if taj else {}
                    except (ValueError, TypeError) as _e:
                        _log.debug("[traceability] tool_args_json 解析失败(§5): %s", _e)
                items.append(item)
            return jsonify(
                {"items": items, "total": total, "limit": limit, "offset": offset}
            )
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
    except Exception as e:
        _log.warning("[tool-execution-log] 查询异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/diff/report")
def _api_diff_report():
    """获取最新的变更对比报告（事实源：MySQL diff_snapshots）。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.diff_engine import diff_engine
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        latest_reqs = diff_engine.load_latest_snapshot("requirements")
        if not latest_reqs:
            return jsonify({"report": None, "message": "无快照数据，请先运行 Step2"})

        versions = diff_snapshot_store.list_versions()
        if len(versions) < 2:
            return jsonify(
                {"report": None, "message": "只有当前版本，无历史版本可对比"}
            )

        old_snapshot = diff_snapshot_store.load_snapshot(
            "requirements", version_label=versions[1]
        )
        if old_snapshot is None:
            return jsonify({"report": None, "message": "无历史快照"})

        req_diff = diff_engine.diff_requirements(latest_reqs, old_snapshot)

        return jsonify(
            {
                "has_changes": req_diff.has_changes,
                "summary": req_diff.summary,
                "entries": [
                    {
                        "entity_type": e.entity_type,
                        "entity_id": e.entity_id,
                        "change_type": e.change_type,
                        "details": e.details,
                    }
                    for e in req_diff.entries[:50]
                ],
                "timestamp": req_diff.timestamp,
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/diff/impact")
def _api_diff_impact():
    """获取变更影响分析。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.diff_engine import diff_engine, impact_analyzer
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store
        from src.extensions.traceability_model import traceability_engine
        from src.core.excel_handler import excel_handler

        latest_reqs = diff_engine.load_latest_snapshot("requirements")
        if not latest_reqs:
            return jsonify({"impact": None, "message": "无快照数据"})

        versions = diff_snapshot_store.list_versions()
        if len(versions) < 2:
            return jsonify({"impact": None, "message": "无历史版本"})

        old_snapshot = diff_snapshot_store.load_snapshot(
            "requirements", version_label=versions[1]
        )
        if old_snapshot is None:
            return jsonify({"impact": None, "message": "历史快照不存在"})
        req_diff = diff_engine.diff_requirements(latest_reqs, old_snapshot)

        trace_links = traceability_engine._load_cache() or {}
        intents = excel_handler.read_test_cases()

        impact = impact_analyzer.analyze(req_diff, trace_links, intents)

        return jsonify(
            {
                "severity": impact.severity,
                "affected_requirements": impact.affected_requirements[:50],
                "affected_test_cases": impact.affected_test_cases[:50],
                "suggestions": impact.suggested_actions,
                "diff_summary": req_diff.summary,
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@docs_bp.route("/api/docs/rag/stats")
def _api_rag_stats():
    """获取 RAG 引擎状态（从 MySQL 直读，跨重启有效）。
    ★ v3: 增加按类型分类统计，前端可点击查看明细。
    ★ v4: by_type 每项含 count/chars/bytes/vectors，支持明细面板展示字数和大小。
    ★ v5: has_embeddings 从 DB 实时查询（vector_blob 非空），不再依赖单例内存状态。
    ★ v6: 增加当前版本 chunks 统计，前端展示"当前版本/总计"格式。
    """
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.extensions.rag_engine import rag_engine

        stats = rag_engine._get_chunk_stats_by_type()
        return jsonify(
            {
                "chunk_count": stats["total"],
                "total_chars": stats.get("total_chars", 0),
                "total_bytes": stats.get("total_bytes", 0),
                "has_embeddings": stats.get("has_embeddings", False),
                "vector_count": stats.get("vector_count", 0),
                "db_ready": stats["total"] > 0,
                "by_type": stats[
                    "types"
                ],  # ★ v4: {"api": {"count": N, "chars": N, "bytes": N, "vectors": N}, ...}
                "current_version_label": stats.get("current_version_label", ""),
                "current_version_chunks": stats.get("current_version_chunks", 0),
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
