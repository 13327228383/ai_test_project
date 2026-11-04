# -*- coding: utf-8 -*-
"""
主题配色方案路由：GET/POST /api/input-style
──────────────────────────
仿 bg_config 模式，存储在 user_credentials_v2 表（project='*'，用户级偏好）。
扩展支持 custom 方案：用户可自定义主色调（hex 格式）。
"""

import json
import logging
import re
from flask import jsonify, request

_log = logging.getLogger(__name__)

_VALID_SCHEMES = {"qingluan", "moyun", "songshi", "custom"}
_DEFAULT_SCHEME = "qingluan"
_DEFAULT_CUSTOM_COLOR = "#00d4aa"

# ★ hex 颜色校验：#rrggbb 格式（不区分大小写）
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_valid_hex(color):
    """校验 hex 颜色格式：必须为 #rrggbb"""
    if not isinstance(color, str):
        return False
    return bool(_HEX_COLOR_RE.match(color.strip()))


def api_input_style():
    """GET/POST /api/input-style — 读取/保存用户主题配色偏好（含 custom 自定义色）。"""
    try:
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401

        # ── GET: 读取 ──
        if request.method == "GET":
            from src.users.credentials import get_user_credential

            val = get_user_credential(uid, "input_style", project="*")
            scheme = _DEFAULT_SCHEME
            custom_color = _DEFAULT_CUSTOM_COLOR
            if val:
                try:
                    data = json.loads(val)
                    if isinstance(data, dict) and data.get("scheme") in _VALID_SCHEMES:
                        scheme = data["scheme"]
                        # ★ custom 方案必须返回 custom_color
                        if scheme == "custom":
                            cc = data.get("custom_color", _DEFAULT_CUSTOM_COLOR)
                            # ★ 兜底校验：历史脏数据可能 hex 格式不合法
                            custom_color = (
                                cc if _is_valid_hex(cc) else _DEFAULT_CUSTOM_COLOR
                            )
                except (json.JSONDecodeError, TypeError):
                    _log.debug(
                        "[input-style] 历史配置 JSON 解析失败，使用默认", exc_info=True
                    )
            return jsonify({"ok": True, "scheme": scheme, "custom_color": custom_color})

        # ── POST: 保存 ──
        data = request.get_json(silent=True) or {}
        scheme = str(data.get("scheme", "")).strip().lower()
        if scheme not in _VALID_SCHEMES:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"无效方案，可选: {', '.join(sorted(_VALID_SCHEMES))}",
                    }
                ),
                400,
            )

        # ★ custom 方案必须附带合法的 custom_color
        custom_color = _DEFAULT_CUSTOM_COLOR
        if scheme == "custom":
            cc = data.get("custom_color", "")
            if not _is_valid_hex(cc):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "custom 方案必须提供合法的 custom_color（#rrggbb 格式）",
                        }
                    ),
                    400,
                )
            custom_color = cc.strip().lower()

        from src.users.credentials import set_user_credential

        payload = {"scheme": scheme}
        if scheme == "custom":
            payload["custom_color"] = custom_color
        set_user_credential(
            uid,
            "input_style",
            json.dumps(payload, ensure_ascii=False),
            project="*",
        )

        try:
            from src.users.manager import log_action

            log_action(
                uid,
                "input-style-save",
                scope_type="user",
                scope_id=uid,
                detail="scheme="
                + scheme
                + (",color=" + custom_color if scheme == "custom" else ""),
            )
        except Exception:
            _log.debug("[input-style] 审计日志写入跳过（非关键）", exc_info=True)

        return jsonify({"ok": True, "scheme": scheme, "custom_color": custom_color})
    except Exception as e:
        _log.error("[api_input_style] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
