# -*- coding: utf-8 -*-
"""
project_config 历史/回滚路由: api_config_history, api_config_rollback
"""

import logging
from src.core.guarded import guard
from flask import jsonify, request

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from .constants import _rebuild_valid_keys_cache


def api_config_history():
    ok, err = check_admin_or_permission(
        "project.config.history", project=_current_project()
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    proj = request.args.get("project") or _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    from src.users.manager import get_config_history

    limit = request.args.get("limit", 50, type=int)
    limit = max(1, min(limit, 200))
    rows = get_config_history(scope_type="project", scope_id=proj, limit=limit)
    return jsonify({"project": proj, "history": rows})


def api_config_rollback():
    ok, err = check_admin_or_permission(
        "project.config.rollback", project=_current_project()
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    data = request.get_json(silent=True) or {}
    proj = data.get("project") or _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    key = data.get("key", "")
    value = data.get("value", "")
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    from src.core.config_loader import get_current_user
    from src.users.manager import rollback_config

    uid = get_current_user() or "system"
    ok, old = rollback_config(proj, key, value, uid)
    if not ok:
        return jsonify({"ok": False, "error": "回滚失败"}), 500
    with guard("api_config_rollback: from src.core.config_loader import c", log=_log, level=logging.WARNING):
        from src.core.config_loader import config as cfg

        cfg._config_data[key] = value
        _rebuild_valid_keys_cache()
        cfg._last_loaded_project = proj
    return jsonify({"ok": True, "key": key, "old": old, "new": value})
