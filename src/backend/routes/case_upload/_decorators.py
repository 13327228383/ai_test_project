# -*- coding: utf-8 -*-
"""case_upload 路由共享装饰器与工具。"""
from functools import wraps

from flask import jsonify

from src.models.permission import has_permission
from src.core.config_loader import get_current_user


def _login_required(fn):
    """要求登录（未登录返回 401）。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        return fn(*args, **kwargs)

    return wrapper


def _require_perm(perm: str):
    """要求系统级权限（无权限返回 403）。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = get_current_user()
            if not uid:
                return jsonify({"ok": False, "error": "请先登录"}), 401
            if not has_permission(uid, perm):
                return jsonify(
                    {"ok": False, "error": f"无权执行此操作（需要权限: {perm}）"}
                ), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _current_target_key() -> str:
    """读取"当前被测系统" target_key。

    优先级：请求参数 target > 全局状态（case_upload.set_current_target 设定的值）。
    两者皆缺省返回空串（引擎不内置任何靶子默认，绝不静默假定某具体系统）。
    注：后续可接入"项目级当前 target"配置。
    """
    from flask import request
    from src.pipeline.steps.step3.case_upload import _CURRENT_TARGET_KEY

    param = (request.values.get("target") or "").strip()
    return param or _CURRENT_TARGET_KEY
