# -*- coding: utf-8 -*-
"""
routes/_save.py — 文件读写类路由
=================================
- api_docs_save  — 文件保存 API
- api_docs_file   — 文件下载 API
"""

import hashlib
import logging
import re
from pathlib import Path
from flask import jsonify, request, send_file
import io

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from src.backend.routes.docs_analytics.docs_analytics import docs_analytics_bp
from src.backend.routes.docs_analytics.fetchers.search import (
    _safe_path,
    _infer_doc_type,
    _auto_version_mysql,
    _EDITABLE,
)

_log = logging.getLogger(__name__)


def register(bp):
    """注册文件读写类路由到 Blueprint。"""

    @bp.route("/api/docs/save", methods=["POST"])
    def api_docs_save():
        """保存编辑后的文件（仅 .md/.json）。★ MySQL 化：写入 source_documents 表。支持乐观锁 + 自动版本化。"""
        ok, err = check_admin_or_permission("docs.edit", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        from src.core.config_loader import config as _cfg
        from src.core.db_manager import get_connection

        data = request.get_json(silent=True) or {}
        rel = data.get("path", "")
        rel = re.sub(r"[\\/]+", "/", rel).strip("/").replace("..", "")
        content = data.get("content", "")
        expected_hash = data.get("source_hash", "")  # ★ 乐观锁：前端传入旧 hash
        # ★ 推断 doc_type（mysql:// URI + 虚拟路径双协议）
        _doc_type = _infer_doc_type(rel)
        # 流水线生成报告只读，不允许在文档中心编辑
        if rel.startswith("mysql://generated_reports/"):
            return jsonify({"ok": False, "error": "生成报告只读，不支持编辑"}), 400
        # ext 优先取 path 后缀；mysql:// URI 无后缀时按 doc_type 映射（confluence/requirement/report→.md，yapi/db_schema/nav_map→.json）
        ext = Path(rel).suffix.lower() if rel else ""
        if not ext:
            from src.backend.routes.docs_analytics.fetchers.mysql_doc import (
                _ext_for_doc_type,
            )

            ext = _ext_for_doc_type(_doc_type)
        if ext not in _EDITABLE:
            return (
                jsonify({"ok": False, "error": "不支持编辑此文件类型 (%s)" % ext}),
                400,
            )
        conn = None
        try:
            import hashlib

            conn = get_connection()
            # ★ active version_label：source_documents PK 为 (project,doc_type,version_label)，
            #   编辑必须 UPSERT 到 active 版本行，避免插入空 version_label 孤立行
            from src.core.versioning import current_version_label

            _vlabel = current_version_label(_current_project()) or ""
            # ★ 乐观锁：如果前端提供了旧 hash，查询 MySQL 比对（按 active version_label）
            if expected_hash:
                row = conn.execute(
                    "SELECT content_hash FROM source_documents "
                    "WHERE project=%s AND doc_type=%s AND version_label=%s",
                    (_cfg.project_name, _doc_type, _vlabel),
                ).fetchone()
                if (
                    row
                    and row.get("content_hash")
                    and row["content_hash"] != expected_hash
                ):
                    return (
                        jsonify(
                            {
                                "error": "文件已被他人修改，请刷新后重新编辑",
                                "conflict": True,
                                "current_hash": row["content_hash"],
                            }
                        ),
                        409,
                    )
            new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            # ★ 自动版本化：保存旧版本到 document_versions
            _auto_version_mysql(
                _current_project(), _doc_type, expected_hash, len(content)
            )
            # ★ MySQL 写入（UPSERT 到 active version_label 行）
            conn.execute(
                "INSERT INTO source_documents(project, doc_type, version_label, content, content_hash, char_count) "
                "VALUES(%s, %s, %s, %s, %s, CHAR_LENGTH(%s)) "
                "ON DUPLICATE KEY UPDATE content=%s, content_hash=%s, char_count=CHAR_LENGTH(%s)",
                (
                    _cfg.project_name,
                    _doc_type,
                    _vlabel,
                    content,
                    new_hash,
                    content,
                    content,
                    new_hash,
                    content,
                ),
            )
            conn.commit()
            _log.info(
                "##HTML:##<span style='color:#4ADE71'>💾 文件已保存到 MySQL: %s (%d 字符)</span>",
                rel,
                len(content),
            )
            return jsonify(
                {"ok": True, "path": rel, "size": len(content), "source_hash": new_hash}
            )
        except Exception as e:
            return jsonify({"ok": False, "error": "保存失败: %s" % str(e)}), 500
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    @bp.route("/api/docs/file")
    def api_docs_file():
        """通用文件下载端点。★ 全 MySQL：resolve_doc 解析后流式下载，无磁盘依赖。"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        from src.backend.routes.docs_analytics.fetchers.mysql_doc import resolve_doc

        rel = request.args.get("path", "")
        doc = resolve_doc(rel, _current_project())
        if not doc:
            return jsonify({"ok": False, "error": "文件不存在"}), 404
        ext = doc["ext"]
        fname = (
            Path(rel).name
            if rel and "/" not in rel
            else (
                "requirements.md"
                if doc["doc_type"] == "requirements"
                else (
                    "test_cases.xlsx"
                    if doc["doc_type"] == "test_cases"
                    else "%s%s" % (doc["doc_type"], ext)
                )
            )
        )
        # 文本类
        if doc["kind"] in ("text", "log"):
            return send_file(
                io.BytesIO((doc["content_text"] or "").encode("utf-8")),
                mimetype="text/markdown; charset=utf-8",
                as_attachment=True,
                download_name=fname,
            )
        # 二进制类（xlsx/xmind/png）
        mimetype = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xmind": "application/zip",
            ".png": "image/png",
        }.get(ext, "application/octet-stream")
        return send_file(
            io.BytesIO(doc["content_bytes"] or b""),
            mimetype=mimetype,
            as_attachment=True,
            download_name=fname,
        )
