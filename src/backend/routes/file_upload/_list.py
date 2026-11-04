# -*- coding: utf-8 -*-
"""
file_upload package — 列表路由
"""

import logging
from flask import request, jsonify

from . import file_upload_bp
from ._decorators import _login_required, FILE_CATEGORIES

_log = logging.getLogger(__name__)


@file_upload_bp.route("/api/step1/uploads", methods=["GET"])
@_login_required
def list_uploaded_files():
    """列出已上传的文件（从 source_documents 查询 upload_* 记录）。"""
    from src.core.config_loader import config

    project_name = (request.args.get("project") or config.project_name or "").strip()
    if not project_name:
        return jsonify({"ok": False, "success": False, "error": "未选择项目"}), 400

    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT doc_type, filename, file_format, category, weight, "
                "content_hash, char_count, created_at "
                "FROM source_documents WHERE project = %s AND doc_type LIKE 'upload_%%' "
                "ORDER BY created_at DESC",
                (project_name,),
            ).fetchall()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        files = []
        for row in rows:
            ch = row.get("content_hash") or ""
            doc_type = row.get("doc_type") or ""
            fmt = row.get("file_format") or ""
            # 回退：老数据 file_format 为空，从 doc_type 提取
            if not fmt and doc_type.startswith("upload_"):
                core = doc_type[len("upload_") :]
                parts = core.rsplit("_", 1)
                fmt = parts[0] if len(parts) > 1 else core
            # 文件名：老数据 filename 为空，用格式+hash 构造
            name = row.get("filename") or ""
            if not name:
                name = "%s 文件 (%s)" % (fmt.upper(), ch[:8] if ch else "无哈希")
            files.append(
                {
                    "id": ch[:16],
                    "filename": name,
                    "format": fmt,
                    "category": row.get("category", "requirement"),
                    "category_label": FILE_CATEGORIES.get(
                        row.get("category", "requirement"), row.get("category", "")
                    ),
                    "weight": float(row["weight"]),
                    "hash": ch[:16],
                    "length": row["char_count"],
                    "created_at": (
                        row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                        if row["created_at"]
                        else ""
                    ),
                }
            )

        return jsonify(
            {"ok": True, "success": True, "files": files, "count": len(files)}
        )
    except Exception as e:
        _log.error("获取上传文件列表异常: %s", e, exc_info=True)
        return (
            jsonify(
                {"ok": False, "success": False, "error": "服务器内部错误，请稍后重试"}
            ),
            500,
        )
