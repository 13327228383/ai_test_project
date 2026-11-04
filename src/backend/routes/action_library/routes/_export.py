# -*- coding: utf-8 -*-
"""
操作指令库 — 导入 / 导出路由
"""

from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)

from src.models.permission import check_admin_or_permission
from src.pipeline.utils.action_library_store import action_library_store
from src.backend.routes.action_library import action_library_bp
from src.backend.state import _current_project


@action_library_bp.route("/api/action-library/export", methods=["GET", "POST"])
def api_export_json():
    """显式导出：把 MySQL 事实源序列化为 JSON 流式下载（服务器不落盘）。"""
    try:
        ok, err = check_admin_or_permission(
            "execution.control", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        import io
        from flask import send_file
        from datetime import datetime

        json_bytes = action_library_store.export_to_json_bytes()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            io.BytesIO(json_bytes),
            mimetype="application/json; charset=utf-8",
            as_attachment=True,
            download_name=f"action_library_{ts}.json",
        )
    except Exception as e:
        _log.error("[action_library/export] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@action_library_bp.route("/api/action-library/import", methods=["POST"])
def api_import_json():
    """显式导入：用上传的 JSON 文件覆盖全局通用 MySQL 事实源（流式，不落盘）。"""
    try:
        ok, err = check_admin_or_permission(
            "execution.control", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        # 优先接受文件上传（流式），也支持通过 path 字段指定服务端本地路径（CLI/自动化场景）
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            text = uploaded.read().decode("utf-8", errors="ignore")
        else:
            body = request.get_json(silent=True) or {}
            path = (body.get("path") or "").strip()
            if not path:
                return jsonify({"ok": False, "error": "缺少上传文件(file)或 path"}), 400
            from pathlib import Path

            try:
                text = Path(path).read_text(encoding="utf-8")
            except Exception as e:
                return (
                    jsonify({"ok": False, "error": "读取文件失败: %s" % str(e)[:120]}),
                    500,
                )
        result = action_library_store.import_from_json_text(text, replace=True)
        result["ok"] = result.get("success", False)
        return jsonify(result), (200 if result["ok"] else 500)
    except Exception as e:
        _log.error("[action_library/import] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
