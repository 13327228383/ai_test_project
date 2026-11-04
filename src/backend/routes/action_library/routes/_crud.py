# -*- coding: utf-8 -*-
"""
操作指令库 — CRUD / 搜索路由
"""

from flask import jsonify, request
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

from src.models.permission import check_admin_or_permission
from src.pipeline.utils.action_library_store import action_library_store
from src.backend.routes.action_library import action_library_bp
from src.backend.routes.action_library._validators import _validate_action
from src.backend.state import _current_project


def _stats_for(lib: dict) -> dict:
    total_categories = len(lib.get("categories", []))
    total_actions = sum(len(c.get("actions", [])) for c in lib.get("categories", []))
    return {"categories": total_categories, "actions": total_actions}


@action_library_bp.route("/api/action-library")
def api_get_library():
    try:
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        # ★ 2026-07-06 恢复从 MySQL 读取用户自定义操作库；仅用于前端只读展示。
        #   代码驱动视图作为兜底，避免 DB 为空时页面空白。
        with guard("api_get_library: lib = action_library_store.load_libr", log=_log, level=logging.WARNING):
            lib = action_library_store.load_library()
            if lib and lib.get("categories"):
                return jsonify(
                    {
                        "ok": True,
                        "scope": "global",
                        "version": lib.get("version", 1),
                        "categories": lib["categories"],
                        "stats": _stats_for(lib),
                    }
                )
        lib = _build_library_from_code()
        return jsonify(
            {
                "ok": True,
                "scope": "global",
                "version": 2,
                "categories": lib["categories"],
                "stats": _stats_for(lib),
            }
        )
    except Exception as e:
        _log.error("[api_get_library] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ── 代码驱动操作库构建 ──
_SCENE_NAMES = {
    "base": ("基础操作", "🔧"),
    "form": ("表单操作", "📝"),
    "interact": ("复杂交互", "🖱"),
    "file": ("文件操作", "📎"),
    "nav": ("导航操作", "🧭"),
    "layout": ("布局操作", "📐"),
    "iframe": ("iframe操作", "🔗"),
    "api": ("API断言", "🔌"),
    "db": ("数据库断言", "🗄"),
    "style": ("样式断言", "🎨"),
    "feedback": ("反馈断言", "💬"),
    "perf": ("性能断言", "⚡"),
}


def _build_library_from_code() -> dict:
    """从 _action_injection.py 的 _OPERATIONS 和 _ASSERTIONS 构建操作库结构。"""
    from src.pipeline.steps.step3.case_generator._action_injection import (
        _OPERATIONS,
        _ASSERTIONS,
    )

    cats_by_scene = {}
    for aid, params, desc, tag in _OPERATIONS:
        if tag not in cats_by_scene:
            name, icon = _SCENE_NAMES.get(tag, (tag, "📌"))
            cats_by_scene[tag] = {"id": tag, "name": name, "icon": icon, "actions": []}
        cats_by_scene[tag]["actions"].append(
            {
                "id": aid,
                "label": desc,
                "desc": desc,
                "keywords": [aid],
                "params": [p + "*" for p in params],
                "is_workflow": False,
                "needs_ai_id": bool(params),
                "applicable_tags": [],
            }
        )

    for aid, params, desc, tag in _ASSERTIONS:
        scene_key = tag if tag in _SCENE_NAMES else "base"
        if scene_key not in cats_by_scene:
            name, icon = _SCENE_NAMES.get(scene_key, (scene_key, "📌"))
            cats_by_scene[scene_key] = {
                "id": scene_key,
                "name": name,
                "icon": icon,
                "actions": [],
            }
        cats_by_scene[scene_key]["actions"].append(
            {
                "id": aid,
                "label": desc,
                "desc": desc,
                "keywords": [aid],
                "params": [p + "*" for p in params],
                "is_workflow": False,
                "needs_ai_id": False,
                "applicable_tags": [],
            }
        )

    # 按预定义顺序排列分类
    ordered = [
        "base",
        "form",
        "interact",
        "file",
        "nav",
        "layout",
        "iframe",
        "api",
        "db",
        "style",
        "feedback",
        "perf",
    ]
    categories = [cats_by_scene[k] for k in ordered if k in cats_by_scene]
    # 追加未在 ordered 中的分类
    for k, v in cats_by_scene.items():
        if k not in ordered:
            categories.append(v)

    return {"version": 2, "categories": categories}


@action_library_bp.route("/api/action-library/action", methods=["POST"])
def api_upsert_action():
    try:
        ok, err = check_admin_or_permission(
            "execution.control", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        body = request.get_json(silent=True)
        if not body:
            return jsonify({"ok": False, "error": "请求体为空"}), 400

        category_id = (body.get("category_id") or "").strip()
        action = body.get("action")
        if not category_id:
            return jsonify({"ok": False, "error": "缺少 category_id"}), 400
        if not isinstance(action, dict) or not action:
            return jsonify({"ok": False, "error": "action 必须是非空 JSON 对象"}), 400
        valid, vmsg = _validate_action(action)
        if not valid:
            _log.warning("[ACTION_LIBRARY] upsert_action 拒绝非法 action: %s", vmsg)
            return jsonify({"ok": False, "error": vmsg}), 400

        clean = action_library_store.upsert_action(
            category_id,
            action,
            category_name=body.get("category_name"),
            category_icon=body.get("category_icon"),
        )
        if not clean:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "写入 MySQL 失败: %s"
                        % (action_library_store.last_error or "unknown"),
                    }
                ),
                500,
            )
        return jsonify(
            {
                "ok": True,
                "message": "已更新",
                "action": clean,
                "category_id": category_id,
            }
        )
    except Exception as e:
        _log.error("[action_library/action] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@action_library_bp.route("/api/action-library/action/<action_id>", methods=["DELETE"])
def api_delete_action(action_id):
    ok, err = check_admin_or_permission("execution.control", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )

    deleted = action_library_store.delete_action(action_id)
    if not deleted:
        return jsonify({"ok": False, "error": f"未找到操作: {action_id}"}), 404
    return jsonify({"ok": True, "message": f"已删除: {action_id}"})


@action_library_bp.route("/api/action-library/search")
def api_search_actions():
    try:
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        q = (request.args.get("q") or "").strip().lower()
        if not q:
            return jsonify({"ok": True, "query": "", "results": [], "total": 0})

        lib = action_library_store.load_library()
        results = []
        for cat in lib.get("categories", []):
            for a in cat.get("actions", []):
                text = " ".join(
                    [
                        str(a.get("id", "")),
                        str(a.get("label", "")),
                        str(a.get("desc", "")),
                        " ".join(str(k) for k in (a.get("keywords") or [])),
                    ]
                ).lower()
                if q in text:
                    results.append(
                        {
                            "action": a,
                            "category_id": cat.get("id"),
                            "category_name": cat.get("name"),
                        }
                    )
        return jsonify(
            {"ok": True, "query": q, "results": results, "total": len(results)}
        )
    except Exception as e:
        _log.error("[api_search_actions] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@action_library_bp.route("/api/action-library/reset", methods=["POST"])
def api_reset_library():
    try:
        ok, err = check_admin_or_permission(
            "execution.control", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        if not action_library_store.reset_to_default():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "重置失败: %s"
                        % (action_library_store.last_error or "unknown"),
                    }
                ),
                500,
            )
        return jsonify({"ok": True, "message": "已重置为默认指令库", "version": 1})
    except Exception as e:
        _log.error("[action_library/reset] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
