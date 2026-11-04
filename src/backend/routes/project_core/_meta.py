# -*- coding: utf-8 -*-
"""
项目元数据 DB 操作路由
"""

from datetime import datetime
from flask import jsonify, request
import logging

from src.backend.routes.project_core import project_core_bp

_log = logging.getLogger(__name__)


_ALLOWED_META_KEYS = frozenset({"display_name", "description", "status", "created_by"})


def _ensure_project_meta_table():
    """确保 project_meta 表存在（2026-06-22：已由 src/core/schema.sql 统一负责，保留此函数签名兼容）。"""
    pass


def _sync_project_meta(project: str, **fields):
    """同步项目元数据到 DB（字段白名单，防止 SQL 注入）。"""
    _ensure_project_meta_table()
    from src.users.manager import _conn

    c = _conn()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for k in _ALLOWED_META_KEYS:
            if k in fields:
                c.execute(
                    f"UPDATE project_meta SET {k}=%s, updated_at=%s WHERE project=%s",
                    (fields[k], now, project),
                )
        # ★ 同步 projects.status：归档/恢复时保持 projects 表与 project_meta 表的 status 一致。
        #   否则 get_user_projects / project_auth / _check / 切换项目检查等 4 处
        #   `projects.status='active'` 过滤全部失效——归档项目仍被当 active 列出且可切换。
        if "status" in fields:
            c.execute(
                "UPDATE projects SET status=%s WHERE name=%s",
                (fields["status"], project),
            )
        exists = c.execute(
            "SELECT 1 FROM project_meta WHERE project=%s", (project,)
        ).fetchone()
        if not exists:
            c.execute(
                "INSERT IGNORE INTO project_meta(project, display_name, description, status, created_by, created_at, updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    project,
                    fields.get("display_name", project),
                    fields.get("description", ""),
                    fields.get("status", "active"),
                    fields.get("created_by", ""),
                    now,
                    now,
                ),
            )
        c.commit()
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)


def _get_project_meta(project: str) -> dict:
    """获取单个项目的元数据。"""
    _ensure_project_meta_table()
    from src.users.manager import _conn

    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM project_meta WHERE project=%s", (project,)
        ).fetchone()
    finally:
        if c:
            try:
                c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    if row:
        return {
            "project": row["project"],
            "display_name": row["display_name"] or row["project"],
            "description": row["description"] or "",
            "status": row["status"] or "active",
            "created_by": row["created_by"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }
    return {
        "project": project,
        "display_name": project,
        "description": "",
        "status": "active",
        "created_by": "",
        "created_at": "",
        "updated_at": "",
    }


def _register_meta_routes():
    @project_core_bp.route("/api/project/meta", methods=["GET"])
    def api_project_meta():
        """获取项目元数据。"""
        from src.backend.state import _current_project
        from src.models.permission import check_admin_or_permission

        try:
            ok, err = check_admin_or_permission(
                "project.config.view", project=_current_project()
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), (
                    401 if err.startswith("请先登录") else 403
                )
            proj = request.args.get("project") or _current_project()
            if not proj:
                return jsonify({"ok": False, "error": "未选择项目"}), 400
            meta = _get_project_meta(proj)
            meta["ok"] = True
            return jsonify(meta)
        except Exception as e:
            _log.error("[api_project_meta] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500

    @project_core_bp.route("/api/project/meta", methods=["POST"])
    def api_project_meta_save():
        """更新项目元数据（display_name, description）。"""
        from src.backend.state import _current_project
        from src.backend.helpers import _user_can_access_project
        from src.models.permission import check_admin_or_permission
        from src.core.config_loader import get_current_user

        ok, err = check_admin_or_permission(
            "project.config.edit", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        data = request.get_json(silent=True) or {}
        proj = data.get("project") or _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        uid = get_current_user()
        if uid and not _user_can_access_project(uid, proj):
            return jsonify({"ok": False, "error": "无权访问项目 %s" % proj}), 403
        display_name = data.get("display_name", "").strip()
        description = data.get("description", "").strip()
        _sync_project_meta(proj, display_name=display_name, description=description)
        from src.users.manager import log_action

        log_action(
            uid,
            "project-meta-save",
            scope_type="project",
            scope_id=proj,
            detail=f"display={display_name}",
        )
        return jsonify({"ok": True})
