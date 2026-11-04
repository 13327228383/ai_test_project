# -*- coding: utf-8 -*-
"""
项目配置路由 —— 配置读写、历史、回滚

【2026-06-18 重构】
原 619 行单体文件已拆分为 project_config/fetchers/ 下的独立模块：
- _constants.py — 所有常量和 _rebuild_valid_keys_cache, _coerce_config_value
- _read.py     — api_get_config (只读路由)
- _write.py    — api_save_config (写入路由)
- _history.py  — api_config_history, api_config_rollback (历史/回滚路由)

本文件保留 Blueprint 和路由注册，导入各 fetcher 实现函数。
"""

import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from flask import Blueprint, jsonify, request
import logging

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

from src.backend.state import _current_project
from src.backend.helpers import _user_can_access_project

project_config_bp = Blueprint("project_config", __name__)

# ── 从 fetchers 导入常量和辅助函数（供外部模块引用）──
from .fetchers.constants import (
    _TYPE_BOOL_KEYS,
    _TYPE_INT_KEYS,
    _TYPE_FLOAT_KEYS,
    _VALID_CONFIG_KEYS,
    _STATIC_CONFIG_KEYS,
    _rebuild_valid_keys_cache,
    _coerce_config_value,
)

# ── 从 fetchers 导入路由实现函数 ──
from .fetchers.read import (
    api_get_config as _api_get_config_impl,
)
from .fetchers.write import (
    api_save_config as _api_save_config_impl,
)
from .fetchers.history import (
    api_config_history as _api_config_history_impl,
    api_config_rollback as _api_config_rollback_impl,
)


# ── 路由注册（@project_config_bp.route 装饰器）──


@project_config_bp.route("/api/config")
def api_get_config():
    return _api_get_config_impl()


@project_config_bp.route("/api/config", methods=["POST"])
def api_save_config():
    return _api_save_config_impl()


@project_config_bp.route("/api/config/history")
def api_config_history():
    return _api_config_history_impl()


@project_config_bp.route("/api/config/rollback", methods=["POST"])
def api_config_rollback():
    return _api_config_rollback_impl()


@project_config_bp.route("/api/proxy/test-connection", methods=["POST"])
def api_proxy_test_connection():
    """后端代理探测目标 URL 可达性，绕过前端 CSP 对 connect-src 的限制。"""
    from src.core.config_loader import get_current_user
    from urllib.parse import urlparse

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    if not _user_can_access_project(uid, proj):
        return jsonify({"ok": False, "error": "无权访问项目 %s" % proj}), 403

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "缺少 url 参数"}), 400

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return jsonify({"ok": False, "error": "仅支持 http/https URL"}), 400

    try:
        start = time.time()
        req = urllib.request.Request(url, method="HEAD")
        # 部分服务器对 HEAD 返回 405，因此允许自动回退到 GET
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                latency_ms = int((time.time() - start) * 1000)
                return jsonify(
                    {
                        "ok": True,
                        "status": resp.status,
                        "latency_ms": latency_ms,
                        "message": f"连接正常 ✓ 延迟 {latency_ms}ms",
                    }
                )
        except urllib.error.HTTPError as e:
            # 405 Method Not Allowed 时回退 GET；其余 HTTP 错误直接返回
            if e.code == 405:
                start = time.time()
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    latency_ms = int((time.time() - start) * 1000)
                    return jsonify(
                        {
                            "ok": True,
                            "status": resp.status,
                            "latency_ms": latency_ms,
                            "message": f"连接正常 ✓ 延迟 {latency_ms}ms",
                        }
                    )
            latency_ms = int((time.time() - start) * 1000)
            return jsonify(
                {
                    "ok": False,
                    "status": e.code,
                    "latency_ms": latency_ms,
                    "error": f"HTTP {e.code}: {e.reason}",
                }
            )
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        return jsonify({"ok": False, "error": f"无法连接: {reason}"})
    except Exception as e:
        _log.error("[api_proxy_test_connection] 探测失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"探测异常: {str(e)[:120]}"})
