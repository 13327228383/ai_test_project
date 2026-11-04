# -*- coding: utf-8 -*-
"""
文档中心 — 查询类路由
========================
报告目录、Figma 统计、测试用例文件列表、执行人列表。
"""

from datetime import datetime
from src.core.guarded import guard
from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission

# ★ P2: MySQL doc URI 前缀走 constants 集中点
from src.core.constants import MYSQL_DOC_URI_REPORT as _REPORT_URI


# ── 报告目录 ──


@docs_bp.route("/api/report-dir")
def api_report_dir():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    req_project = request.args.get("project", "").strip()
    _project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    # ★ MySQL 化：报告已入 generated_reports，返回 MySQL 标识（不再返回磁盘路径）
    # ★ P2: MySQL doc URI 走 constants
    return jsonify({"path": _REPORT_URI})


# ── Figma 统计 ──


@docs_bp.route("/api/docs/figma-stats")
def api_docs_figma_stats():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project, _temporary_project

    req_project = request.args.get("project", "").strip()
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    stats = {
        "page_count": 0,
        "canvas_count": 0,
        "component_count": 0,
        "node_count": 0,
        "design_updated_at": "",
        "is_synced": False,
        "file_name": "",
        "size_mb": 0.0,
        "fetch_age_hours": 0,
        "is_stale": False,
        "quota_used": 0,
        "quota_limit": 0,
    }
    try:
        from src.pipeline.steps.step1.figma_cache import get_figma_cache_info

        # ★ get_figma_cache_info 依赖全局项目，需临时切换
        with _temporary_project(project_name):
            cache = get_figma_cache_info(force=True)
        if cache.get("has_cache"):
            stats["page_count"] = cache.get("page_count", 0)
            stats["canvas_count"] = cache.get("canvas_count", 0)
            stats["component_count"] = cache.get("component_count", 0)
            stats["node_count"] = cache.get("node_count", 0)
            stats["file_name"] = cache.get("file_name", "")
            stats["size_mb"] = round(cache.get("size_mb", 0), 1)
            stats["fetch_age_hours"] = round(cache.get("fetch_age_hours", 0), 1)
            stats["is_stale"] = cache.get("is_stale", False)
            stats["design_updated_at"] = cache.get("design_modified", "")
            stats["is_synced"] = cache.get("is_synced", False)
        # 配额信息
        with guard("api_docs_figma_stats: from src.pipeline.steps.step1.mcp.fi", log=_log):
            from src.pipeline.steps.step1.mcp.figma_mcp import quota_tracker

            qs = quota_tracker.get_status()
            stats["quota_used"] = qs.get("used", 0)
            stats["quota_limit"] = qs.get("limit", 0)
    except Exception as e:
        import traceback

        traceback.print_exc()
        _log.error("[figma-stats] ERROR: %s", e)
    return jsonify(stats)


# ── 全量测试用例文件列表 ──


@docs_bp.route("/api/docs/intent-asset-files")
def api_docs_case_asset_files():
    """返回全量测试用例文件列表（不含核心验证测试用例，核心验证用独立 API）。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"files": []}), (401 if err.startswith("请先登录") else 403)
    if not _current_project():
        return jsonify({"files": []})
    # ★ 全走 MySQL：测试用例在 test_cases 表，返回当前版本汇总（取代扫磁盘 xlsx 列表）
    files = []
    with guard("api_docs_case_asset_files: from src.pipeline.utils.case_store i", log=_log, level=logging.WARNING):
        from src.pipeline.utils.case_store import case_store

        intents = case_store.list_intents() or []
        if intents:
            files.append(
                {
                    "name": "Validation_Intent_current.xlsx",
                    "size": 0,
                    "mtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ext": ".xlsx",
                }
            )
    return jsonify({"files": files})


# ── 执行人列表 ──


@docs_bp.route("/api/docs/executors")
def api_docs_executors():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"executors": []}), (401 if err.startswith("请先登录") else 403)
    if not _current_project():
        return jsonify({"executors": []})
    try:
        from src.users.manager import get_project_executors

        executors = get_project_executors(_current_project())
        return jsonify({"executors": executors})
    except Exception as e:
        return jsonify({"executors": [], "error": str(e)})
