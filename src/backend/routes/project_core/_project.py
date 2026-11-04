# -*- coding: utf-8 -*-
"""
项目管理路由 —— 创建、列表、切换
"""

from datetime import datetime
from src.core.guarded import guard
from pathlib import Path
from flask import jsonify, request
import logging

from src.backend.routes.project_core import project_core_bp
from src.backend.routes.project_core._meta import _sync_project_meta, _get_project_meta

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def _reload_project_context(project_name: str) -> None:
    """重载当前项目上下文。

    项目列表接口不应被这个重载阻塞；需要时由后台线程调用。
    """
    with guard("_reload_project_context: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config as cfg

        cfg.reload(project_name)
        from src.core.excel_handler import excel_handler

        excel_handler.reload_paths()
        from src.core.session_context import session_ctx

        session_ctx.reload_for_project()


def _reload_project_context_async(project_name: str) -> None:
    """后台重载项目上下文，避免列表/创建接口阻塞前端。"""
    import threading as _th

    _th.Thread(
        target=_reload_project_context, args=(project_name,), daemon=True
    ).start()


def _register_project_routes():
    @project_core_bp.route("/api/new-project", methods=["POST"])
    def api_new_project():
        from src.core.config_loader import get_current_user
        from src.users.manager import is_admin
        from src.models.permission import has_permission

        uid = get_current_user() or ""
        if not is_admin(uid) and not has_permission(uid, "project.create"):
            return (
                jsonify(
                    {"ok": False, "error": "无权创建项目（需要权限: project.create）"}
                ),
                403,
            )

        raw_data = request.get_json(silent=True) or {}
        from src.backend.pydantic_schemas import validate_body, NewProjectSchema

        data, schema_err = validate_body(NewProjectSchema)
        if schema_err:
            return schema_err
        name = data.get("name", "").strip()
        # ★ 格式校验已由 NewProjectSchema(pattern=...) + validate_body 统一处理
        #    不再在路由层重复 regex，避免前后端校验不一致
        # ★ 全走 MySQL：项目存在性查 projects 表，不再创建 data/{name}/ 目录
        from src.users.manager import _conn

        _c = _conn()
        try:
            if _c.execute("SELECT 1 FROM projects WHERE name=%s", (name,)).fetchone():
                return jsonify({"ok": False, "error": "项目 %s 已存在" % name}), 409
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _c.execute(
                "INSERT IGNORE INTO projects(name, status, created_at) VALUES(%s,%s,%s)",
                (name, "active", now),
            )
            _c.commit()
        finally:
            _c.close()
        display_name = raw_data.get("display_name", name)
        description = raw_data.get("description", "")
        _sync_project_meta(
            name, display_name=display_name, description=description, created_by=uid
        )
        with guard("api_new_project: from src.users.manager import create", log=_log, level=logging.WARNING):
            from src.users.manager import create_project_version

            default_ver = raw_data.get("version", "").strip() or "v1.0"
            vid = create_project_version(name, default_ver, "初始版本", uid, "active")
            if vid:
                _log.info(f"[Project] 默认版本已创建: {name}/{default_ver} (id={vid})")
        with guard("api_new_project: from src.users.manager import assign", log=_log, level=logging.ERROR):
            from src.users.manager import assign_project

            ok = assign_project(name, uid, "owner")
            if ok:
                _log.info(
                    f"[Project] 创建者 {uid} 已设为项目 {name} 的 owner（project_members 表）"
                )
            else:
                _log.error(f"[Project] 项目 owner 指派失败: {uid} → {name}")
        from src.users.manager import log_action

        log_action(
            uid,
            "create-project",
            scope_type="project",
            scope_id=name,
            detail=f"display={display_name}",
        )

        # ★ 新建项目后自动切换到新项目，避免用户手动去列表翻找
        from src.backend.state import _set_current_project

        _set_current_project(name)
        try:
            from flask import session as _sess

            _sess["project_name"] = name
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        # ★ 新建即记为用户最近项目（登录自动恢复依据）
        try:
            from src.users.manager import set_user_last_project

            set_user_last_project(uid, name)
        except Exception as _pass_e2:
            _log.warning("非关键异常: %s", _pass_e2, exc_info=True)
        # ★ 配置重载移至后台线程，避免 /api/new-project 响应阻塞前端。
        _reload_project_context_async(name)

        return jsonify({"ok": True, "project": name})

    @project_core_bp.route("/api/projects")
    def api_projects():
        """获取用户可访问的项目列表 — 项目级配置全部从数据库读取。"""
        from src.backend.state import _current_project, _set_current_project
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        projects = []
        if uid:
            from src.users.manager import get_user_projects

            projects = sorted(get_user_projects(uid))

        cur = _current_project()
        # ★ 用户级会话恢复：F5 / 服务器重启后，从持久化 session 还原用户项目选择
        #    避免读到其他用户写入的全局变量（多用户互相覆盖）或 projects[0] 兜底（用户原选丢失）。
        if uid and projects:
            with guard("api_projects: from flask import session as _sess", log=_log, level=logging.WARNING):
                from flask import session as _sess

                _sess_proj = _sess.get("project_name")
                if _sess_proj and _sess_proj in projects and _sess_proj != cur:
                    _set_current_project(_sess_proj)  # 同步写回全局，供后台任务使用
                    _reload_project_context_async(_sess_proj)
                    cur = _sess_proj

        if uid and projects and (not cur or cur not in projects):
            # ★ 根因(2026-08-31)：不再静默 auto-assign projects[0]，与 _middleware 4b 保持一致。
            #   旧实现在「用户未选项目」时把排序后的第一个项目强加给用户，导致 KPI 总览/控制台
            #   显示的不是用户真正选择的项目，且登录闸门永远有 current 而失效。改为：无会话项目时
            #   返回空 current，由前端把用户引流到「项目管理」先显式选择/新建项目才能进入系统。
            cur = ""
        elif uid and not projects:
            # 彻底修复：新用户无项目是正常空态，读列表接口必须快速返回；
            # 不重载空项目配置，避免首次登录被无意义的 cfg.reload("") 拖到前端超时。
            _set_current_project("")
            cur = ""

        from src.users.manager import get_active_version_label, is_admin

        project_list = []
        for p in projects:
            meta = _get_project_meta(p)
            active_ver = get_active_version_label(p)
            project_list.append({"name": p, "active_version": active_ver, **meta})

        # ★ admin 可见归档项目（带 status='archived' 标记），供项目管理面板恢复/删除；
        #   非 admin 仅见 active（归档项目对普通用户不可见，与 get_user_projects 一致）。
        #   projects（可切换列表）仍只含 active，归档项目不进下拉避免切换 404。
        if uid and is_admin(uid):
            with guard("api_projects: from src.users.manager import _conn", log=_log, level=logging.WARNING):
                from src.users.manager import _conn

                _ac = _conn()
                try:
                    archived = _ac.execute(
                        "SELECT name FROM projects WHERE status='archived' ORDER BY name"
                    ).fetchall()
                finally:
                    try:
                        _ac.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
                for r in archived:
                    pn = r["name"]
                    if pn in projects:
                        continue
                    meta = _get_project_meta(pn)
                    project_list.append({"name": pn, "active_version": "", **meta})

        return jsonify(
            {
                "ok": True,
                "projects": projects,
                "current": cur,
                "project_list": project_list,
            }
        )

    @project_core_bp.route("/api/project", methods=["POST"])
    def api_switch_project():
        from src.backend.state import (
            _current_project,
            _set_current_project,
            _is_running,
            _get_paused_step,
        )
        from src.backend.helpers import _user_can_access_project
        from src.core.config_loader import get_current_user

        data = request.get_json(silent=True) or {}
        # ★ 2026-09-22 同族归一化：本端点历史读 name，而兄弟端点（/api/step/<n> 等）
        #   一律用 project —— 脚本天然发 project 撞"项目名不能为空"。两键都收。
        name = str(data.get("name") or data.get("project") or "").strip()
        if not name:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "缺少字段：需 name（或别名 project）指定项目名",
                    }
                ),
                400,
            )
        from src.users.manager import _conn

        _c = _conn()
        exists = _c.execute(
            "SELECT 1 FROM projects WHERE name=%s AND status='active'", (name,)
        ).fetchone()
        _c.close()
        if not exists:
            return jsonify({"ok": False, "error": "项目 %s 不存在" % name}), 404
        uid = get_current_user()
        if uid and not _user_can_access_project(uid, name):
            return jsonify({"ok": False, "error": "无权访问项目 %s" % name}), 403
        # ★ 仅在任务真正运行时阻止切换（线程活跃且未暂停）
        if _is_running(name) and _get_paused_step(name) <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "目标项目有任务正在执行中，无法切换。请先停止或等待完成",
                    }
                ),
                409,
            )
        # ★ A1 改造：检查当前项目是否在运行（不能在运行中切换走）
        from src.backend.state import _current_project as _cur_proj_fn

        _cur = _cur_proj_fn()
        if _cur and _cur != name and _is_running(_cur) and _get_paused_step(_cur) <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "当前项目有任务正在执行中，无法切换项目。请先停止或等待完成",
                    }
                ),
                409,
            )
        _set_current_project(name)
        # ★ 记忆用户最近项目（登录自动恢复依据）
        try:
            from src.users.manager import set_user_last_project

            set_user_last_project(uid, name)
        except Exception as _pass_e2:
            _log.warning("非关键异常: %s", _pass_e2, exc_info=True)
        reload_errors = []
        try:
            from src.core.config_loader import config as cfg

            cfg.reload(name, force=True)
        except Exception as e:
            _log.warning("[WARN] 配置重载失败: %s", e)
            reload_errors.append("config: " + str(e)[:100])
        try:
            from src.core.excel_handler import excel_handler

            excel_handler.reload_paths()
        except Exception as e:
            _log.warning("[WARN] excel_handler 重载失败: %s", e)
            reload_errors.append("excel: " + str(e)[:100])
        try:
            from src.core.session_context import session_ctx

            session_ctx.reload_for_project()
        except Exception as e:
            _log.warning("[WARN] session_ctx 重载失败: %s", e)
            reload_errors.append("session_ctx: " + str(e)[:100])

        # ★ 切换项目后清理该用户的所有 API 缓存，确保新项目数据立刻生效
        with guard("api_switch_project: from src.backend._middleware import ", log=_log):
            from src.backend._middleware import invalidate_api_cache_for_user
            from src.core.config_loader import get_current_user

            uid = get_current_user()
            invalidate_api_cache_for_user(uid)

        # ★ 切项目自动套用算法推荐值（用户偏好开关，默认关闭 → 零行为变更）
        # 此时 config.project_name 已是新项目，推荐值按新项目上下文计算。
        # 失败不影响切换结果。
        with guard("api_switch_project: from src.backend.routes.pipeline_con", log=_log, level=logging.WARNING):
            from src.backend.routes.pipeline_config._auto_apply import (
                is_auto_apply_enabled,
                apply_recommended_to_user,
            )

            if is_auto_apply_enabled(uid):
                applied = apply_recommended_to_user(uid)
                _log.info(
                    "[project-switch] 已为用户 %s 自动套用 %d 项推荐值（项目 %s）",
                    uid, applied, name,
                )

        result = {"ok": True, "project": name}
        if reload_errors:
            result["warnings"] = reload_errors
        return jsonify(result)
