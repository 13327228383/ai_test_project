# -*- coding: utf-8 -*-
"""删除路由（占位）：/api/data-models/<model_id> (DELETE)"""

import logging
from flask import jsonify

from src.backend.routes.data_models import data_models_bp

_log = logging.getLogger(__name__)


def _register_delete_routes():
    @data_models_bp.route("/api/data-models/<model_id>", methods=["DELETE"])
    def api_data_models_delete(model_id):
        """删除指定数据模型。"""
        try:
            return jsonify({"ok": True, "deleted": model_id})
        except Exception as e:
            _log.error("[api_data_models_delete] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500
