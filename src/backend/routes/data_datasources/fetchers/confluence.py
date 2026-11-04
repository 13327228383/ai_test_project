# -*- coding: utf-8 -*-
"""
Confluence 统计/刷新路由处理函数。
──────────────────────────
api_confluence_stats, api_confluence_refresh
"""

import logging
from src.core.guarded import guard
import time

from flask import jsonify

from src.backend.state import _current_project

_log = logging.getLogger(__name__)


def api_confluence_stats(_check_login_and_project):
    """GET /api/confluence-stats — 返回 Confluence 状态（不触发网络请求）。"""
    ok, err = _check_login_and_project()
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.config_loader import config
        from src.core.db_manager import get_connection, release_connection

        # ★ fix: 按当前项目重新加载配置，避免读到上一个项目的配置残留
        from src.backend.state import _current_project

        _proj = _current_project()
        if _proj:
            with guard("api_confluence_stats: config.reload(_proj)", log=_log, level=logging.WARNING):
                config.reload(_proj)

        cf_page_ids = config.confluence_page_ids or []
        if not cf_page_ids:
            return jsonify(
                {
                    "has_data": False,
                    "not_configured": True,
                    "message": "Confluence 未配置 — 请在侧边栏📄需求源配置中设置页面 ID",
                }
            )

        mode = config.confluence_mode
        if mode == "mcp":
            has_pat = bool(config.confluence_pat)
            if not has_pat:
                return jsonify(
                    {
                        "has_data": False,
                        "need_credentials": True,
                        "reason": "no_credentials",
                        "message": "Confluence MCP 模式缺少 PAT (CONFLUENCE_PASSWORD)",
                    }
                )
        else:
            has_user = bool(config.confluence_username)
            has_pass = bool(config.confluence_password)
            if not has_user or not has_pass:
                return jsonify(
                    {
                        "has_data": False,
                        "need_credentials": True,
                        "reason": "no_credentials",
                        "message": "Confluence REST 模式缺少用户名或密码",
                    }
                )

        # 检查缓存
        cache_info = ""
        try:
            proj = _current_project() or ""
            _conn = get_connection()
            try:
                _row = _conn.execute(
                    "SELECT updated_at FROM source_documents "
                    "WHERE project=%s AND doc_type='confluence' ORDER BY updated_at DESC LIMIT 1",
                    (proj,),
                ).fetchone()
            finally:
                try:
                    release_connection(_conn)
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            if _row and _row["updated_at"]:
                _ts = _row["updated_at"]
                if hasattr(_ts, "timestamp"):
                    _ts = _ts.timestamp()
                age_min = (time.time() - float(_ts)) / 60
                if age_min < 60:
                    cache_info = "缓存 %d min前" % int(age_min)
                else:
                    cache_info = "缓存 %.0f h前" % (age_min / 60)
            else:
                cache_info = "无缓存"
        except Exception as e:
            _log.warning("[confluence-stats] 缓存信息获取失败: %s", e)
            cache_info = ""

        return jsonify(
            {
                "has_data": True,
                "mode": mode,
                "page_count": len(cf_page_ids),
                "cache_info": cache_info,
            }
        )
    except Exception as e:
        _log.error("[confluence-stats] 异常: %s", e, exc_info=True)
        return jsonify({"has_data": False, "error": "服务器内部错误"}), 500


def api_confluence_refresh():
    """POST /api/confluence-refresh — 触发 Confluence 后台刷新（项目级 admin 权限）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_project_permission
    from src.backend.state import _current_project

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    if not is_admin(uid) and not has_project_permission(
        uid, proj, "datasource.confluence.refresh"
    ):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "无权刷新 Confluence（项目内需要: datasource.confluence.refresh）",
                }
            ),
            403,
        )
    import threading

    def _task():
        # ★ 新线程必须恢复项目上下文，否则 ConfluenceClient 读取空 config
        from src.backend.state import _set_current_project
        from src.core.config_loader import config as _cfg

        _set_current_project(proj)
        _cfg.reload(proj)
        with guard("_task: from src.pipeline.steps.step1.conflu", log=_log, level=logging.INFO):
            from src.pipeline.steps.step1.confluence_client import confluence_client

            r = confluence_client.fetch_confluence_all()
            ok_flag = r.get("success") if isinstance(r, dict) else bool(r)
            if ok_flag:
                _log.info(
                    "##HTML:##<span style='color:#22c55e'>✓ Confluence 刷新成功</span>"
                )
            else:
                _log.error(
                    "##HTML:##<span style='color:#ef4444'>✗ Confluence 刷新失败: %s</span>",
                    r.get("error", "未知") if isinstance(r, dict) else "未知",
                )

    threading.Thread(target=_task, daemon=True).start()
    return jsonify({"ok": True, "message": "Confluence 刷新已触发（后台执行）"})
