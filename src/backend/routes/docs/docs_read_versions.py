# -*- coding: utf-8 -*-
"""
文档中心 — 文档版本管理路由
==========================
"""

from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission

ALLOWED_DOC_TYPES = {
    "requirement",
    "breakdown",
    "nav-map",
    "yapi",
    "db_schema",
    "test_intents",
    "figma",
}


@docs_bp.route("/api/docs/versions")
def api_doc_versions():
    """文档版本列表。?type=requirement|nav-map|breakdown"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    req_project = request.args.get("project", "").strip()
    proj, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    doc_type = request.args.get("type", "requirement")
    if doc_type not in ALLOWED_DOC_TYPES and not doc_type.startswith("upload_"):
        return jsonify({"ok": False, "error": "无效的文档类型: %s" % doc_type}), 400
    from src.users.manager import get_document_versions

    versions = get_document_versions(proj, doc_type)
    return jsonify({"project": proj, "doc_type": doc_type, "versions": versions})


@docs_bp.route("/api/docs/version/<version_id>")
def api_doc_version_content(version_id):
    """获取指定版本的文件内容。?type=requirement"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    req_project = request.args.get("project", "").strip()
    proj, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    doc_type = request.args.get("type", "requirement")
    if doc_type not in ALLOWED_DOC_TYPES and not doc_type.startswith("upload_"):
        return jsonify({"ok": False, "error": "无效的文档类型: %s" % doc_type}), 400
    from src.users.manager import get_document_versions

    versions = get_document_versions(proj, doc_type, limit=200)
    target = next((v for v in versions if v["version"] == version_id), None)
    if not target:
        return jsonify({"ok": False, "error": "版本不存在"}), 404
    # ★ 全 MySQL：file_path 存的是 mysql:// URI（或旧文件路径），统一用 resolve_doc 解析
    from src.backend.routes.docs_analytics.fetchers.mysql_doc import resolve_doc

    doc = resolve_doc(target.get("file_path", ""), proj)
    if not doc or doc.get("kind") not in ("text", "log"):
        return jsonify({"ok": False, "error": "版本内容不可读"}), 404
    content = doc["content_text"] or ""
    return jsonify({"version_id": version_id, "content": content, "size": len(content)})
