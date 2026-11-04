# -*- coding: utf-8 -*-
"""
Figma 配额/刷新路由处理函数。
─────────────────────
api_figma_quota, api_figma_refresh
"""

import logging
from flask import jsonify

from src.models.permission import check_admin_or_permission
from src.backend.state import _current_project

_log = logging.getLogger(__name__)


def api_figma_quota():
    """GET /api/figma-quota — 返回 Figma API 配额状态，供前端弹窗确认。"""
    try:
        proj = _current_project()
        ok, err = check_admin_or_permission("docs.view", project=proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.pipeline.steps.step1.mcp.figma_mcp import quota_tracker

        check = quota_tracker.should_warn_before_fetch()
        check["ok"] = True
        return jsonify(check)
    except Exception as e:
        _log.exception("[API] /api/figma-quota 异常")
        return (
            jsonify({"ok": False, "error": "服务器内部错误: " + str(e), "status": 500}),
            500,
        )


def api_figma_refresh():
    """POST /api/figma-refresh — 触发 Figma 后台刷新（项目级 admin 权限）。"""
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
        uid, proj, "datasource.figma.refresh"
    ):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "无权刷新 Figma（项目内需要: datasource.figma.refresh）",
                }
            ),
            403,
        )
    import threading

    _log.info("[API] /api/figma-refresh 被调用")
    from src.backend.executor import _figma_refresh_task

    threading.Thread(target=_figma_refresh_task, args=(proj,), daemon=True).start()
    return jsonify({"ok": True, "message": "Figma 刷新已触发（后台执行）"})


def _figma_retry_failed_task(figma_url: str, version_label: str = "") -> dict:
    """同步执行：批量重试最近一次失败的 Figma 节点，返回标准结果字典。"""
    try:
        from src.backend.helpers import _confirm_figma_fetch
        from src.pipeline.steps.step1.smart_figma_fetcher import smart_figma_fetcher

        if not _confirm_figma_fetch():
            return {
                "success": False,
                "message": "Figma 拉取确认被拒绝",
                "retried": 0,
                "remaining_failed": 0,
            }
        if version_label:
            smart_figma_fetcher.set_version(version_label)
        r = smart_figma_fetcher.retry_failed_nodes(figma_url)
        if r.get("success"):
            _log.info(
                "##HTML:##<span style='color:#22c55e'>✓ Figma 失败节点重试成功</span>"
            )
        else:
            err = r.get("error", "未知")
            _log.error(
                "##HTML:##<span style='color:#ef4444'>✗ Figma 失败节点重试失败: %s</span>",
                err,
            )
        return {
            "success": bool(r.get("success")),
            "message": r.get("message") or r.get("error", "未知"),
            "retried": int(r.get("retried_count", 0) or 0),
            "remaining_failed": int(r.get("remaining_failed", 0) or 0),
        }
    except Exception as e:
        import traceback

        _log.error(
            "##HTML:##<span style='color:#ef4444'>✗ Figma 重试异常: %s</span>", e
        )
        traceback.print_exc()
        return {
            "success": False,
            "message": "Figma 重试异常: %s" % str(e)[:120],
            "retried": 0,
            "remaining_failed": 0,
        }


def api_figma_retry_failed():
    """POST /api/figma-retry-failed — 批量重试最近失败的 Figma 节点（项目级 admin 权限）。"""
    from src.core.config_loader import config, get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_project_permission
    from src.backend.state import _current_project

    uid = get_current_user()
    if not uid:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "请先登录",
                    "retried": 0,
                    "remaining_failed": 0,
                }
            ),
            200,
        )
    proj = _current_project()
    if not proj:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "未选择项目",
                    "retried": 0,
                    "remaining_failed": 0,
                }
            ),
            200,
        )
    if not is_admin(uid) and not has_project_permission(
        uid, proj, "datasource.figma.refresh"
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "无权刷新 Figma（项目内需要: datasource.figma.refresh）",
                    "retried": 0,
                    "remaining_failed": 0,
                }
            ),
            200,
        )
    figma_url = config.figma_url
    if not figma_url:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "未配置 Figma URL",
                    "retried": 0,
                    "remaining_failed": 0,
                }
            ),
            200,
        )
    from src.users.project_auth import get_active_version_label

    version_label = get_active_version_label(proj)
    _log.info("[API] /api/figma-retry-failed 被调用")
    result = _figma_retry_failed_task(figma_url, version_label)
    return jsonify(result), 200


def api_figma_images():
    """GET /api/figma-images — 列出当前项目的 Figma 图片节点元信息。

    可选参数: version_label, page_id, node_id, limit
    """
    from flask import request

    try:
        proj = _current_project()
        ok, err = check_admin_or_permission("docs.view", project=proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        from src.pipeline.utils.figma_image_store import list_figma_images

        version_label = request.args.get("version_label", "")
        page_id = request.args.get("page_id", "")
        node_id = request.args.get("node_id", "")
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            limit = 100
        items = list_figma_images(
            proj,
            version_label=version_label,
            page_id=page_id,
            node_id=node_id,
            limit=limit,
        )
        return jsonify({"ok": True, "items": items, "count": len(items)})
    except Exception as e:
        _log.exception("[API] /api/figma-images 异常")
        return jsonify({"ok": False, "error": "服务器内部错误: " + str(e)}), 500


def api_figma_image_data(image_id):
    """GET /api/figma-image/<int:image_id> — 返回单张图片的二进制流。

    用于前端 <img src="/api/figma-image/123"> 直接渲染。
    """
    try:
        proj = _current_project()
        ok, err = check_admin_or_permission("docs.view", project=proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.pipeline.utils.figma_image_store import get_figma_image_data

        try:
            iid = int(image_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "image_id 无效"}), 400
        data = get_figma_image_data(iid)
        if not data:
            return jsonify({"ok": False, "error": "图片不存在"}), 404
        from flask import Response

        # 简单按 magic bytes 判断类型
        mime = "image/png"
        if data[:4] == b"\x89PNG":
            mime = "image/png"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        elif data[:2] == b"\xff\xd8":
            mime = "image/jpeg"
        elif data[:5] == b"<?xml" or data.startswith(b"<svg"):
            mime = "image/svg+xml"
        return Response(data, mimetype=mime)
    except Exception as e:
        _log.exception("[API] /api/figma-image 异常")
        return jsonify({"ok": False, "error": "服务器内部错误: " + str(e)}), 500
