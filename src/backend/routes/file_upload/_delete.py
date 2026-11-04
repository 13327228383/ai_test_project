# -*- coding: utf-8 -*-
"""
file_upload package — 删除与更新权重路由
"""

import logging
from flask import request, jsonify

from . import file_upload_bp
from ._decorators import _login_required

_log = logging.getLogger(__name__)


@file_upload_bp.route("/api/step1/uploads/<file_hash>", methods=["DELETE"])
@_login_required
def delete_uploaded_file(file_hash: str):
    """删除已上传的记录（根据 content_hash 前缀匹配）。"""
    try:
        from src.core.db_manager import get_connection
        from src.core.config_loader import config

        project_name = (
            request.args.get("project") or config.project_name or ""
        ).strip()
        if not project_name:
            return jsonify({"ok": False, "success": False, "error": "未选择项目"}), 400
        # ★ 权限校验：仅项目管理员（file.upload）可删除文件
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("file.upload", project_name)
        if not ok:
            return jsonify({"ok": False, "success": False, "error": err}), 403
        conn = None
        try:
            conn = get_connection()
            conn.execute(
                "DELETE FROM source_documents WHERE project = %s "
                "AND doc_type LIKE 'upload_%%' AND content_hash LIKE %s",
                (project_name, file_hash + "%"),
            )
            conn.commit()
            return jsonify({"ok": True, "success": True})
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    except Exception as e:
        _log.error("删除上传文件异常: %s", e, exc_info=True)
        return (
            jsonify(
                {"ok": False, "success": False, "error": "服务器内部错误，请稍后重试"}
            ),
            500,
        )


@file_upload_bp.route("/api/step1/uploads/<file_hash>/weight", methods=["PUT"])
@_login_required
def update_file_weight(file_hash: str):
    """更新文件权重（根据 content_hash 前缀匹配）。"""
    from src.core.config_loader import config

    project_name = (request.args.get("project") or config.project_name or "").strip()
    if not project_name:
        return jsonify({"ok": False, "success": False, "error": "未选择项目"}), 400
    # ★ 权限校验：仅项目管理员（file.upload）可修改权重
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("file.upload", project_name)
    if not ok:
        return jsonify({"ok": False, "success": False, "error": err}), 403
    data = request.get_json() or {}
    weight = data.get("weight")
    if weight is None:
        return (
            jsonify({"ok": False, "success": False, "error": "缺少 weight 参数"}),
            400,
        )

    weight = float(weight)
    weight = max(0.0, min(1.0, weight))

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE source_documents SET weight = %s WHERE project = %s "
                "AND doc_type LIKE 'upload_%%' AND content_hash LIKE %s",
                (weight, project_name, file_hash + "%"),
            )
            conn.commit()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        return jsonify({"ok": True, "success": True, "weight": weight})
    except Exception as e:
        _log.error("更新文件权重异常: %s", e, exc_info=True)
        return (
            jsonify(
                {"ok": False, "success": False, "error": "服务器内部错误，请稍后重试"}
            ),
            500,
        )
