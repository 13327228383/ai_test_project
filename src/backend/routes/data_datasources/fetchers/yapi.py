# -*- coding: utf-8 -*-
"""
YApi 统计/刷新路由处理函数。
───────────────────
api_yapi_stats, api_yapi_refresh
"""

import logging
from flask import jsonify

_log = logging.getLogger(__name__)


def api_yapi_stats(_check_login_and_project):
    """GET /api/yapi-stats — 返回 YApi 接口统计数据。"""
    ok, err = _check_login_and_project()
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        from src.core.config_loader import config
        from src.pipeline.steps.step1.yapi_client import yapi_client

        # ★ 第1层：URL 是否已配置
        if not config.api_docs_enabled:
            return jsonify(
                {
                    "has_data": False,
                    "not_configured": True,
                    "message": "接口文档地址未配置 — 请点击侧边栏🔌接口配置",
                }
            )

        # ★ 第2层：凭据是否已配置（不发起网络请求，快速检测）
        check = yapi_client.check_configured()
        if not check["configured"]:
            return jsonify(
                {
                    "has_data": False,
                    "need_credentials": True,
                    "reason": check["reason"],
                    "message": check["message"],
                }
            )

        # ★ 第3层：尝试调用 YApi API
        result = yapi_client.fetch_stats()
        if result.get("success"):
            return jsonify({"has_data": True, **result["data"]})
        error_msg = result.get("error", "未知错误")
        reason = ""
        if "连接" in error_msg or "connection" in error_msg.lower():
            reason = "connection_error"
        elif "认证" in error_msg or "登录" in error_msg or "auth" in error_msg.lower():
            reason = "auth_failed"
        elif "配置" in error_msg and "账号" in error_msg:
            reason = "no_credentials"
        elif "地址" in error_msg:
            reason = "not_configured"
        else:
            reason = "api_error"
        return jsonify({"has_data": False, "error": error_msg, "reason": reason})
    except Exception as e:
        err_str = str(e)
        if "connection" in err_str.lower() or "timeout" in err_str.lower():
            _log.info(f"[INFO] yapi-stats: 网络不通（{err_str[:60]}）")
            return jsonify(
                {
                    "has_data": False,
                    "error": "YApi 服务无法连接 — 请检查 API_DOCS_URL 地址和网络",
                    "reason": "connection_error",
                }
            )
        _log.error(f"[ERROR] yapi-stats: {e}")
        return jsonify({"has_data": False, "error": "服务器内部错误"})


def api_yapi_refresh():
    """POST /api/yapi-refresh — 触发 YApi 后台刷新（项目级 admin 权限）。"""
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
        uid, proj, "datasource.yapi.refresh"
    ):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "无权刷新 YApi（项目内需要: datasource.yapi.refresh）",
                }
            ),
            403,
        )
    import threading

    def _task():
        # ★ 新线程必须恢复项目上下文，否则 PerProjectRegistry 返回默认空实例
        from src.backend.state import _set_current_project
        from src.core.config_loader import config as _cfg

        _set_current_project(proj)
        _cfg.reload(proj)
        try:
            from src.pipeline.steps.step1.yapi_client import yapi_client

            r = yapi_client.fetch_stats()
            if r.get("success"):
                d = r["data"]
                _log.info(
                    "##HTML:##<span style='color:#22c55e'>✓ YApi 刷新成功: %s — %d 个接口</span>",
                    d.get("project_name", "?"),
                    d.get("total", 0),
                )
            else:
                _log.error(
                    "##HTML:##<span style='color:#ef4444'>✗ YApi 刷新失败: %s</span>",
                    r.get("error", "未知"),
                )
        except Exception as e:
            _log.error(
                "##HTML:##<span style='color:#ef4444'>✗ YApi 刷新异常: %s</span>",
                e,
                exc_info=True,
            )

    threading.Thread(target=_task, daemon=True).start()
    return jsonify({"ok": True, "message": "YApi 刷新已触发（后台执行）"})
