# -*- coding: utf-8 -*-
"""详情路由（占位）：/api/data-models/<model_id>"""

import logging
from flask import jsonify

from src.backend.routes.data_models import data_models_bp

_log = logging.getLogger(__name__)


def _register_detail_routes():
    @data_models_bp.route("/api/data-models/<model_id>", methods=["GET"])
    def api_data_models_detail(model_id):
        """返回指定数据模型的详情。"""
        try:
            return jsonify({"ok": True, "model_id": model_id})
        except Exception as e:
            _log.error("[api_data_models_detail] 异常: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": "服务器内部错误"}), 500
