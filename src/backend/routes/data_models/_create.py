# -*- coding: utf-8 -*-
"""创建/更新路由：/api/task-model, /api/verification-model, /api/vision-model (POST)"""

import logging
from src.core.guarded import guard
from flask import request, jsonify

from src.backend.routes.data_models import data_models_bp
from src.backend.state import _current_project

_log = logging.getLogger(__name__)

_HTTP_FORBIDDEN = 403
_HTTP_BAD_REQUEST = 400


def _register_create_routes():
    from src.models.permission import check_admin_or_permission

    @data_models_bp.route("/api/task-model", methods=["POST"])
    def api_set_task_model():
        try:
            ok, err = check_admin_or_permission(
                "project.config.edit", project=_current_project()
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), _HTTP_FORBIDDEN
            data = request.get_json(silent=True) or {}
            model = data.get("model", "")
            if not model:
                return (
                    jsonify({"ok": False, "error": "请提供 model"}),
                    _HTTP_BAD_REQUEST,
                )

            # ★ 1. 写入内存（立即生效）
            from src.core.config_loader import config

            config._config_data["AI_MODEL"] = model
            config._config_data["DEEPSEEK_MODEL"] = model

            # ★ 2. 持久化到用户级凭据（project="*"，切换项目不丢失）
            with guard("api_set_task_model: from src.core.config_loader import g", log=_log, level=logging.WARNING):
                from src.core.config_loader import get_current_user
                from src.users.manager import set_user_credential

                uid = get_current_user()
                if uid:
                    set_user_credential(uid, "API_MODEL", model, project="*")

            return jsonify({"ok": True, "model": model})
        except Exception as e:
            _log.error(f"[ERROR] task-model: {e}")
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @data_models_bp.route("/api/verification-model", methods=["POST"])
    def api_set_verification_model():
        """保存用户选择的校验模型（持久化到用户级凭据）。"""
        try:
            ok, err = check_admin_or_permission(
                "project.config.edit", project=_current_project()
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), _HTTP_FORBIDDEN
            data = request.get_json(silent=True) or {}
            model = data.get("model", "")
            if not model:
                return (
                    jsonify({"ok": False, "error": "请提供 model"}),
                    _HTTP_BAD_REQUEST,
                )

            # ★ 1. 写入内存（立即生效）
            from src.core.config_loader import config

            config._config_data["VERIFICATION_MODEL"] = model

            # ★ 2. 持久化到用户级凭据（project="*"，切换项目不丢失）
            with guard("api_set_verification_model: from src.core.config_loader import g", log=_log, level=logging.WARNING):
                from src.core.config_loader import get_current_user
                from src.users.manager import set_user_credential

                uid = get_current_user()
                if uid:
                    set_user_credential(uid, "VERIFICATION_MODEL", model, project="*")

            return jsonify({"ok": True, "model": model})
        except Exception as e:
            _log.error(f"[ERROR] verification-model: {e}")
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @data_models_bp.route("/api/vision-model", methods=["POST"])
    def api_set_vision_model():
        try:
            # 视觉模型保存到用户级 (project="*")，不要求项目权限
            data = request.get_json(silent=True) or {}
            model = data.get("model", "")
            enabled = data.get("enabled")
            # ★ 视觉来源：cloud 云端 / ollama 本地
            vision_provider = data.get("vision_provider") or "cloud"
            if vision_provider not in ("cloud", "ollama"):
                vision_provider = "cloud"
            # ★ Fix: 视觉模型独立 API key / base_url
            vision_api_key = data.get("vision_api_key", "").strip()
            vision_base_url = data.get("vision_base_url", "").strip().strip("`").strip()
            if (
                not model
                and enabled is None
                and not vision_api_key
                and not vision_base_url
            ):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "请提供 model / enabled / vision_api_key / vision_base_url",
                        }
                    ),
                    _HTTP_BAD_REQUEST,
                )

            # ★ 1. 写入内存（立即生效）
            from src.core.config_loader import config

            # ollama 本地模型存 VISION_LOCAL_MODEL，云端模型存 VISION_MODEL
            if model:
                if vision_provider == "ollama":
                    config._config_data["VISION_LOCAL_MODEL"] = model
                    config._config_data.pop("VISION_MODEL", None)
                else:
                    config._config_data["VISION_MODEL"] = model
                    config._config_data.pop("VISION_LOCAL_MODEL", None)
                config._config_data["VISION_PROVIDER"] = vision_provider
            if enabled is not None:
                config._config_data["VISION_MODEL_ENABLED"] = bool(enabled)
            if vision_api_key:
                config._config_data["VISION_API_KEY"] = vision_api_key
            if vision_base_url:
                config._config_data["VISION_BASE_URL"] = vision_base_url

            # ★ 2. 持久化到用户级凭据（project="*"，切换项目不丢失）
            with guard("api_set_vision_model: from src.core.config_loader import g", log=_log, level=logging.WARNING):
                from src.core.config_loader import get_current_user
                from src.users.manager import set_user_credential

                uid = get_current_user()
                if uid:
                    if model:
                        if vision_provider == "ollama":
                            set_user_credential(uid, "VISION_LOCAL_MODEL", model, project="*")
                            set_user_credential(uid, "VISION_MODEL", "", project="*")
                        else:
                            set_user_credential(uid, "VISION_MODEL", model, project="*")
                            set_user_credential(uid, "VISION_LOCAL_MODEL", "", project="*")
                        set_user_credential(uid, "VISION_PROVIDER", vision_provider, project="*")
                    if enabled is not None:
                        set_user_credential(
                            uid,
                            "VISION_MODEL_ENABLED",
                            str(int(bool(enabled))),
                            project="*",
                        )
                    if vision_api_key:
                        set_user_credential(
                            uid, "VISION_API_KEY", vision_api_key, project="*"
                        )
                    if vision_base_url:
                        set_user_credential(
                            uid, "VISION_BASE_URL", vision_base_url, project="*"
                        )

            return jsonify({"ok": True, "model": model, "enabled": enabled})
        except Exception as e:
            _log.error(f"[ERROR] vision-model: {e}")
            return jsonify({"ok": False, "error": str(e)[:200]}), 500
