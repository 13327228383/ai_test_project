# -*- coding: utf-8 -*-
"""
GET /api/step3/case/uploads — 查询上传历史（T08）。
"""
import logging

from flask import jsonify, request

from . import case_upload_bp
from ._decorators import _login_required

_log = logging.getLogger(__name__)


@case_upload_bp.route("/api/step3/case/uploads", methods=["GET"])
@_login_required
def list_case_uploads():
    """查询用例上传历史。

    Query params:
        - limit: 返回条数（默认 50，最大 200）
        - target: 按被测系统过滤（可选）

    Returns:
        {"ok": bool, "items": [...], "count": int}
    """
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        target = (request.args.get("target") or "").strip()
        from src.backend.case_upload_history import list_recent

        items = list_recent(limit=limit, target_key=target)
        return jsonify({"ok": True, "items": items, "count": len(items)})
    except Exception as e:
        _log.warning("[case_upload] 历史查询失败: %s", e)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
