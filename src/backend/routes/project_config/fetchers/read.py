# -*- coding: utf-8 -*-
"""
project_config 只读路由: api_get_config
"""

import logging
from flask import jsonify, request

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from src.backend.helpers import _user_can_access_project

ALLOWED_KEY_PREFIXES = (
    "AI_",
    "OLLAMA_",
    "STORAGE_",
    "EXCEL_",
    "VERIFICATION_",
    "DEEPSEEK_",
    "OPENAI_",
    "ANTHROPIC_",
    "NVIDIA_",
    "FIGMA_",
    "CONFLUENCE_",
    "API_DOCS_",
    "TARGET_",
    "DB_",
    "confidence_",
    "requirement_gate_",
    "rag_",
    "browser_",
    "risk_",
    "dedup_",
    "step2_",
    "baseline_health_",
    "confidence_weight_",
    "PROJECT_VERSION",
)


def api_get_config():
    proj = request.args.get("project") or _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if uid and not _user_can_access_project(uid, proj):
        return jsonify({"ok": False, "error": "无权访问项目 %s" % proj}), 403

    keys_str = request.args.get("keys", "")
    requested_keys = (
        [x for k in keys_str.split(",") if (x := k.strip())] if keys_str else None
    )
    if requested_keys:
        for k in requested_keys:
            if not any(k.startswith(prefix) for prefix in ALLOWED_KEY_PREFIXES):
                return jsonify({"ok": False, "error": "不允许的配置键: %s" % k}), 400

    try:
        from src.core.config_loader import config as cfg

        cfg.reload(proj)
        if requested_keys:
            fields = {}
            for k in requested_keys:
                v = cfg._get(k)
                if v is not None:
                    fields[k] = v
            if "PROJECT_VERSION" in requested_keys:
                try:
                    from src.users.manager import get_active_version_label

                    ver = get_active_version_label(proj)
                    if ver:
                        fields["PROJECT_VERSION"] = ver
                except Exception as _e:
                    _log.debug("import: %s", _e, exc_info=True)
        else:
            all_config = cfg.get_all()
            if not all_config:
                fields = {}
            else:
                fields = dict(all_config)
                try:
                    from src.core.config_loader import ConfigLoader

                    for short_key, long_key in ConfigLoader._YAML_KEY_MAP.items():
                        if long_key in fields and short_key not in fields:
                            fields[short_key] = fields[long_key]
                except Exception as _e:
                    _log.debug("import + load: %s", _e, exc_info=True)
            try:
                from src.users.manager import get_active_version_label

                ver = get_active_version_label(proj)
                if ver:
                    fields["PROJECT_VERSION"] = ver
            except Exception as _e:
                _log.debug("import: %s", _e, exc_info=True)
    except Exception as e:
        _log.error("[api_get_config] ConfigLoader 加载失败: %s", e, exc_info=True)
        fields = {}

    str_fields = {}
    for k, v in fields.items():
        if isinstance(v, bool):
            str_fields[k] = "true" if v else "false"
        elif isinstance(v, (int, float)):
            str_fields[k] = str(v)
        elif isinstance(v, (list, tuple)):
            str_fields[k] = ", ".join(str(x) for x in v)
        else:
            str_fields[k] = str(v)

    # ★ Fix55a: 为已知的数字/浮数字段回填默认值（前端不展示默认值的问题）
    try:
        from .constants import _TYPE_INT_KEYS, _TYPE_FLOAT_KEYS, _KNOWN_DEFAULTS

        for k in _TYPE_INT_KEYS | _TYPE_FLOAT_KEYS:
            if k not in str_fields or not str_fields[k]:
                default_val = cfg._get(k)
                # ★ 属性默认值兜底：cfg._get 返回 None 时回退到 _KNOWN_DEFAULTS
                if default_val is None and k in _KNOWN_DEFAULTS:
                    default_val = _KNOWN_DEFAULTS[k]
                if default_val is not None:
                    str_fields[k] = str(default_val)
    except Exception as _e:
        _log.debug("默认值回填失败: %s", _e, exc_info=True)

    return jsonify({"project": proj, "fields": str_fields})
