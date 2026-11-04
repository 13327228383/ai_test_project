# -*- coding: utf-8 -*-
"""
归档/恢复路由 —— 项目归档、物理删除、恢复
"""

import shutil
from src.core.guarded import guard
from pathlib import Path
from flask import jsonify, request
import logging

from src.backend.routes.project_core import project_core_bp
from src.backend.routes.project_core._meta import _sync_project_meta

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def _register_archive_routes():
    @project_core_bp.route("/api/project/<name>", methods=["DELETE"])
    def api_delete_project(name):
        """归档或删除项目。默认归档（status='archived'），?hard=1 物理删除。"""
        from src.backend.state import _current_project
        from src.backend.helpers import _user_can_access_project
        from src.core.config_loader import get_current_user
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("project.delete", project=name)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        uid = get_current_user()
        if uid and not _user_can_access_project(uid, name):
            return jsonify({"ok": False, "error": "无权操作项目 %s" % name}), 403
        hard = request.args.get("hard", "0") == "1"
        # ★ 全走 MySQL：项目存在性查 projects 表，不再检查/删除 data/{name}/ 目录
        from src.users.manager import _conn

        _pc = _conn()
        try:
            exists = _pc.execute(
                "SELECT 1 FROM projects WHERE name=%s", (name,)
            ).fetchone()
        finally:
            try:
                _pc.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        if not exists:
            return jsonify({"ok": False, "error": "项目 %s 不存在" % name}), 404

        if name == _current_project():
            return (
                jsonify(
                    {"ok": False, "error": "不能删除当前选中项目，请先切换到其他项目"}
                ),
                409,
            )

        if hard:
            # ★ 全走 MySQL：hard 删除只清数据库，不再 shutil.rmtree(data/{name}/)
            from src.users.manager import _conn, log_action
            from src.users.project_auth import cleanup_project_roles

            # ★ v3: 清理项目成员关系（project_members 表）。
            #   cleanup_project_roles 已重写为 DELETE FROM project_members WHERE project=?
            #   不再生成 proj_{name}_* 角色，无需清理孤儿角色。
            with guard("api_delete_project: cleanup_project_roles(name)", log=_log, level=logging.WARNING):
                cleanup_project_roles(name)
            c = _conn()
            try:
                c.execute("DELETE FROM projects WHERE name=%s", (name,))
                c.execute("DELETE FROM project_meta WHERE project=%s", (name,))
                c.commit()
            finally:
                if c:
                    try:
                        c.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            log_action(
                uid,
                "project-delete",
                scope_type="project",
                scope_id=name,
                detail="hard",
            )
            return jsonify({"ok": True, "action": "deleted", "project": name})
        else:
            _sync_project_meta(name, status="archived")
            from src.users.manager import log_action

            log_action(uid, "project-archive", scope_type="project", scope_id=name)
            return jsonify({"ok": True, "action": "archived", "project": name})

    @project_core_bp.route("/api/project/<name>/restore", methods=["POST"])
    def api_restore_project(name):
        """恢复已归档的项目。"""
        from src.backend.helpers import _user_can_access_project
        from src.core.config_loader import get_current_user
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("project.delete", project=name)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        uid = get_current_user()
        if uid and not _user_can_access_project(uid, name):
            return jsonify({"ok": False, "error": "无权操作项目 %s" % name}), 403
        _sync_project_meta(name, status="active")
        from src.users.manager import log_action

        log_action(uid, "project-restore", scope_type="project", scope_id=name)
        return jsonify({"ok": True, "project": name})
