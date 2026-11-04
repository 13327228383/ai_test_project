# -*- coding: utf-8 -*-
"""
关系链路统一 API（07-05h 新增）
================================

提供测试用例↔原子需求↔需求切片↔figma/api/dom/数据库 的完整关系链路查看。

3 个统一端点：
  - GET /api/trace/req/<req_id>   — 按需求ID展开完整链路
  - GET /api/trace/case/<case_id> — 按用例ID展开完整链路
  - GET /api/trace/slice/<slice_id> — 按切片ID展开关联需求+用例

链路节点：
  - 测试用例 (test_cases)
  - 原子需求 (requirements)
  - 需求切片 (requirement_slices) ★ 新增
  - Figma 页面 (system_feature_list + rag_chunks type=figma_page)
  - API 文档 (rag_chunks type=api)
  - DOM 快照 (nav_graph_nodes，原 page_dom_snapshots 已于 2026-08-05 DROP)
  - 数据库表 (rag_chunks type=db_table)
  - 追溯链路 (trace_links)
"""

import logging
from src.core.guarded import guard
from flask import jsonify

from src.models.permission import check_admin_or_permission
from src.backend.state import _current_project

_log = logging.getLogger(__name__)


def _register_trace_routes(bp):
    """注册关系链路路由到指定 Blueprint。"""

    @bp.route("/api/trace/req/<req_id>")
    def _api_trace_by_req(req_id: str):
        """按需求ID展开完整关系链路。

        返回:
        {
            "requirement": {...需求详情},
            "slice": {...切片详情} or null,
            "test_cases": [{...用例列表}],
            "trace_link": {...追溯链路} or null,
            "figma_pages": [{...关联Figma页面}],
            "api_endpoints": [{...关联API端点}],
            "db_tables": [{...关联数据库表}],
            "dom_snapshots": [{...关联DOM快照}]
        }
        """
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "请先选择项目"}), 400
        if not req_id:
            return jsonify({"ok": False, "error": "req_id 不能为空"}), 400

        try:
            result = _build_trace_by_req(req_id)
            return jsonify(result)
        except Exception as e:
            _log.error("[trace/req] 异常: %s", e, exc_info=True)
            return (
                jsonify({"ok": False, "error": "服务器内部错误: %s" % str(e)[:200]}),
                500,
            )

    @bp.route("/api/trace/case/<case_id>")
    def _api_trace_by_case(case_id: str):
        """按用例ID展开完整关系链路。

        返回:
        {
            "test_case": {...用例详情},
            "requirement": {...关联需求} or null,
            "slice": {...关联切片} or null,
            "trace_link": {...追溯链路} or null,
            "figma_pages": [...],
            "api_endpoints": [...],
            "db_tables": [...]
        }
        """
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "请先选择项目"}), 400
        if not case_id:
            return jsonify({"ok": False, "error": "case_id 不能为空"}), 400

        try:
            result = _build_trace_by_case(case_id)
            return jsonify(result)
        except Exception as e:
            _log.error("[trace/case] 异常: %s", e, exc_info=True)
            return (
                jsonify({"ok": False, "error": "服务器内部错误: %s" % str(e)[:200]}),
                500,
            )

    @bp.route("/api/trace/slice/<slice_id>")
    def _api_trace_by_slice(slice_id: str):
        """按切片ID展开关联需求+用例。"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "请先选择项目"}), 400
        if not slice_id:
            return jsonify({"ok": False, "error": "slice_id 不能为空"}), 400

        try:
            result = _build_trace_by_slice(slice_id)
            return jsonify(result)
        except Exception as e:
            _log.error("[trace/slice] 异常: %s", e, exc_info=True)
            return (
                jsonify({"ok": False, "error": "服务器内部错误: %s" % str(e)[:200]}),
                500,
            )


# ══════════════════════════════════════════════════
# 链路构造函数
# ══════════════════════════════════════════════════


def _build_trace_by_req(req_id: str) -> dict:
    """按需求ID构造完整链路。"""
    # 1. 读取需求详情
    requirement = _get_requirement(req_id)
    if not requirement:
        return {"error": "需求 %s 不存在" % req_id, "requirement": None}

    # 2. 读取关联切片（单一事实源：slice_id）
    slice_info = None
    slice_id = requirement.get("slice_id") or ""
    if slice_id:
        slice_info = _get_slice(slice_id)

    # 3. 读取关联用例
    test_cases = _get_cases_by_req(req_id)

    # 4. 读取追溯链路（api/db_tables/figma_pages/related_reqs）
    trace_link = _get_trace_link(req_id)

    # 5. 展开 figma/api/db/dom
    figma_pages = _get_figma_pages(trace_link, requirement)
    api_endpoints = _get_api_endpoints(trace_link)
    db_tables = _get_db_tables(trace_link)
    dom_snapshots = _get_dom_snapshots_by_req(requirement)
    # ★ H1: 补全追溯链路最后一环 — source_documents（切片→原文档）
    source_document = _get_source_document(slice_info, requirement)

    return {
        "requirement": requirement,
        "slice": slice_info,
        "test_cases": test_cases,
        "trace_link": trace_link,
        "figma_pages": figma_pages,
        "api_endpoints": api_endpoints,
        "db_tables": db_tables,
        "dom_snapshots": dom_snapshots,
        "source_document": source_document,
    }


def _build_trace_by_case(case_id: str) -> dict:
    """按用例ID构造完整链路。"""
    test_case = _get_case(case_id)
    if not test_case:
        return {"error": "用例 %s 不存在" % case_id, "test_case": None}

    # 通过 case_requirement_mapping 找关联需求
    req_id = _get_req_id_by_case(case_id)
    requirement = None
    slice_info = None
    trace_link = None
    if req_id:
        requirement = _get_requirement(req_id)
        if requirement:
            slice_id = requirement.get("slice_id") or ""
            if slice_id:
                slice_info = _get_slice(slice_id)
        trace_link = _get_trace_link(req_id)

    return {
        "test_case": test_case,
        "requirement": requirement,
        "slice": slice_info,
        "trace_link": trace_link,
        "figma_pages": (
            _get_figma_pages(trace_link, requirement)
            if trace_link or requirement
            else []
        ),
        "api_endpoints": _get_api_endpoints(trace_link) if trace_link else [],
        "db_tables": _get_db_tables(trace_link) if trace_link else [],
        "source_document": (
            _get_source_document(slice_info, requirement) if requirement else {}
        ),
    }


def _build_trace_by_slice(slice_id: str) -> dict:
    """按切片ID构造关联需求+用例。"""
    slice_info = _get_slice(slice_id)
    if not slice_info:
        return {"error": "切片 %s 不存在" % slice_id, "slice": None}

    # 查找从该切片提取的所有需求
    requirements = _get_reqs_by_slice(slice_id)

    # 收集所有关联用例
    all_cases = []
    for req in requirements:
        cases = _get_cases_by_req(req.get("req_id", ""))
        all_cases.extend(cases)

    return {
        "slice": slice_info,
        "requirements": requirements,
        "test_cases": all_cases,
    }


# ══════════════════════════════════════════════════
# 数据读取辅助函数
# ══════════════════════════════════════════════════


def _get_requirement(req_id: str) -> dict:
    """读取需求详情（含 payload_json 中的 _chunk_id/_chunk_text/feature_label）。

    ★ 性能修复：改用 requirement_store.get_requirement(req_id) 单条 SQL 查询，
      避免原 list_requirements() 全表扫描 + Python for 循环过滤（N+1 性能瓶颈）。
    """
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        return requirement_store.get_requirement(req_id) or {}
    except Exception as e:
        _log.warning("[trace] _get_requirement 失败: %s", e)
        return {}


def _get_slice(slice_id: str) -> dict:
    """读取切片详情。"""
    try:
        from src.pipeline.utils.requirement_slice_store import requirement_slice_store

        return requirement_slice_store.get_slice(slice_id) or {}
    except Exception as e:
        _log.warning("[trace] _get_slice 失败: %s", e)
        return {}


def _get_cases_by_req(req_id: str) -> list:
    """按 req_id 读取关联用例列表。"""
    if not req_id:
        return []
    try:
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

        case_ids = case_requirement_tracker.get_intents_for_req(req_id)
        cases = []
        for cid in case_ids:
            case = _get_case(cid)
            if case:
                cases.append(case)
        return cases
    except Exception as e:
        _log.warning("[trace] _get_cases_by_req 失败: %s", e)
        return []


def _get_case(case_id: str) -> dict:
    """读取单条用例。"""
    if not case_id:
        return {}
    try:
        from src.pipeline.utils.case_store import case_store

        return case_store.get_intent(case_id) or {}
    except Exception as e:
        _log.warning("[trace] _get_case 失败: %s", e)
        return {}


def _get_req_id_by_case(case_id: str) -> str:
    """通过 case_requirement_mapping 反查 req_id。"""
    try:
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker

        return case_requirement_tracker.get_req_id(case_id) or ""
    except Exception as e:
        _log.warning("[trace] _get_req_id_by_case 失败: %s", e)
        return ""


def _get_reqs_by_slice(slice_id: str) -> list:
    """按切片ID查找关联需求（单一事实源：slice_id）。"""
    try:
        from src.pipeline.utils.requirement_store import requirement_store

        reqs = requirement_store.list_requirements()
        return [r for r in reqs if r.get("slice_id") == slice_id]
    except Exception as e:
        _log.warning("[trace] _get_reqs_by_slice 失败: %s", e)
        return []


def _get_trace_link(req_id: str) -> dict:
    """读取需求的追溯链路。"""
    if not req_id:
        return {}
    try:
        from src.pipeline.utils.trace_link_store import trace_link_store

        return trace_link_store.get_link_by_req_id(req_id) or {}
    except Exception as e:
        _log.warning("[trace] _get_trace_link 失败: %s", e)
        return {}


def _get_figma_pages(trace_link: dict, requirement: dict) -> list:
    """获取关联的 Figma 页面。"""
    if not trace_link:
        return []
    pages = trace_link.get("figma_pages") or []
    if not isinstance(pages, list):
        return []
    # 补充 Figma 详情
    result = []
    for p in pages:
        if isinstance(p, str):
            result.append({"page_name": p, "page_id": ""})
        elif isinstance(p, dict):
            result.append(p)
    return result


def _get_api_endpoints(trace_link: dict) -> list:
    """获取关联的 API 端点。"""
    if not trace_link:
        return []
    endpoints = trace_link.get("api_endpoints") or []
    if not isinstance(endpoints, list):
        return []
    return endpoints


def _get_db_tables(trace_link: dict) -> list:
    """获取关联的数据库表。"""
    if not trace_link:
        return []
    tables = trace_link.get("db_tables")
    if isinstance(tables, dict):
        return [{"table_name": k, "info": v} for k, v in tables.items()]
    elif isinstance(tables, list):
        return tables
    return []


def _get_dom_snapshots_by_req(requirement: dict) -> list:
    """按需求关联的页面URL读取 DOM 快照（粗略关联）。"""
    # 简化实现：返回空列表（DOM 快照关联复杂，后续按需扩展）
    return []


def _get_source_document(slice_info: dict, requirement: dict) -> dict:
    """★ H1: 补全追溯链路最后一环 — 切片/需求 → source_documents 原文档。

    链路: test_cases → requirements → requirement_slices → source_documents
    关联键: slice.doc_type (或 slice_id 前缀 / requirement.source_anchor) → source_documents.doc_type

    Returns:
        {doc_type, filename, source_url, category, char_count} 或 {}（未找到时）
    """
    # ★ None 安全：slice_info 在需求无 slice_id 时为 None（调用方 L144 仅在命中时赋值），
    #   requirement 缺失分支同理。本函数契约是"未找到 → {}"，对 None 入参必须同样返回 {}。
    slice_info = slice_info if isinstance(slice_info, dict) else {}
    requirement = requirement if isinstance(requirement, dict) else {}
    project = _current_project()
    if not project:
        return {}
    # 推断 doc_type：优先 slice_info.doc_type → slice_id 前缀 → requirement.source_anchor
    doc_type = ""
    if slice_info:
        doc_type = str(slice_info.get("doc_type") or "").strip()
    if not doc_type:
        _slice_id = str(slice_info.get("slice_id") or requirement.get("slice_id") or "")
        if ":" in _slice_id:
            doc_type = _slice_id.split(":")[0]
    if not doc_type:
        _anchor = str(requirement.get("source_anchor") or "")
        if ":" in _anchor:
            doc_type = _anchor.split(":")[0]
    if not doc_type:
        return {}
    with guard("_get_source_document: from src.users.project_auth import get_", log=_log, level=logging.WARNING):
        from src.users.project_auth import get_active_version_label

        version_label = get_active_version_label(project)
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT doc_type, filename, source_url, category, char_count "
                "FROM source_documents "
                "WHERE project=%s AND version_label=%s AND doc_type=%s "
                "ORDER BY updated_at DESC LIMIT 1",
                (project, version_label, doc_type),
            ).fetchone()
            if row:
                return {
                    "doc_type": str(row.get("doc_type", "")),
                    "filename": str(row.get("filename", "")),
                    "source_url": str(row.get("source_url", "")),
                    "category": str(row.get("category", "")),
                    "char_count": int(row.get("char_count", 0) or 0),
                }
        finally:
            conn.close()
    return {}
