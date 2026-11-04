# -*- coding: utf-8 -*-
"""清除文件路由：/api/clear-files"""

from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)


def _register_clear_routes():
    from src.backend.routes.execution import exec_bp
    from src.backend.state import _current_project
    from src.backend.executor import _clear_files

    @exec_bp.route("/api/clear-files", methods=["POST"])
    def api_clear_files():
        from src.core.config_loader import get_current_user
        from src.users.manager import log_action, is_admin
        from src.models.permission import has_project_permission

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        proj = _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        # 项目级权限校验：admin 或项目内 admin 才能清除
        if not is_admin(uid) and not has_project_permission(uid, proj, "file.clear"):
            return (
                jsonify(
                    {"ok": False, "error": "无权清除中间文件（项目内需要: file.clear）"}
                ),
                403,
            )
        # ★ 移除 API Key 强制要求 — 清除文件不需要 AI 调用
        data = request.get_json(silent=True) or {}
        targets = data.get("targets")  # 旧版：分类名列表
        files = data.get("files")  # 新版：具体文件路径列表
        all_derived = bool(data.get("all_derived"))  # 全量派生数据清空（输入态保留）
        n_cleared, n_errors = _clear_files(
            targets=targets, file_paths=files, all_derived=all_derived
        )
        log_action(
            uid,
            "clear-files",
            scope_type="project",
            scope_id=proj,
            detail=f"all_derived={all_derived} targets={targets} files={len(files) if files else 0} cleared={n_cleared} errors={n_errors}",
        )
        return jsonify({"ok": True, "cleared": n_cleared, "errors": n_errors})
