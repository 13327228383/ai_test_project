# -*- coding: utf-8 -*-
"""
系统级配置 API（跨项目共享）
============================
GET  /api/system-config  — 读取全部系统配置
POST /api/system-config  — 更新系统配置（批量 key-value）

数据来源: config.yaml（系统级默认值）+ system_config 表（覆盖值）。
config.yaml 提供基础值，system_config 表中的同 key 值覆盖 config.yaml。
不依赖项目选择，与项目级 /api/config 完全隔离。
"""

import logging
from pathlib import Path
from flask import Blueprint, jsonify, request

_log = logging.getLogger(__name__)
system_cfg_bp = Blueprint("system_config", __name__)

# ★ 归一化分类单一事实源：分组 + 字段元数据由后端 schema 提供，前端只负责渲染
from src.core.system_config_schema import build_system_config_schema  # noqa: E402


def _get_sys_conn():
    from src.core.db_manager import get_connection

    return get_connection()


def _release_sys_conn(conn):
    from src.core.db_manager import release_connection

    release_connection(conn)


def _flatten_yaml_to_uppercase(
    section: str, value, prefix: str = "", result: dict = None
) -> dict:
    """将嵌套 YAML 字典平铺为 UPPERCASE 键名，与 ConfigLoader._flatten_yaml_to_uppercase 一致。"""
    if result is None:
        result = {}
    key_prefix = (prefix + "_" + section).upper() if prefix else section.upper()
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten_yaml_to_uppercase(k, v, key_prefix, result)
    elif isinstance(value, (list, tuple)):
        result[key_prefix] = value
    else:
        result[key_prefix] = value
    return result


def _load_config_yaml_values() -> dict:
    """加载 config.yaml 系统级配置（排除 projects 节），平铺为 UPPERCASE 键名。"""
    yaml_path = Path(__file__).resolve().parent.parent.parent / "core" / "config.yaml"
    if not yaml_path.is_file():
        return {}
    try:
        import yaml as _yaml

        with open(str(yaml_path), "r", encoding="utf-8") as f:
            raw = _yaml.safe_load(f) or {}
        result = {}
        for key, value in raw.items():
            if key == "projects":
                continue
            _flatten_yaml_to_uppercase(key, value, "", result)
        return result
    except Exception as e:
        _log.warning("[system-config] config.yaml 加载失败: %s", e)
        return {}


@system_cfg_bp.route("/api/system-config", methods=["GET"])
def api_get_system_config():
    """读取全部系统级配置：config.yaml 默认值 + system_config 表覆盖。"""
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("system.settings")
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    conn = None
    try:
        # 1. 加载 config.yaml 系统级默认值（基础层）
        result = _load_config_yaml_values()

        # 2. system_config 表值覆盖（覆盖层）
        conn = _get_sys_conn()
        rows = conn.execute(
            "SELECT `key`, `value` FROM system_config ORDER BY `key`"
        ).fetchall()
        for r in rows or []:
            result[r["key"]] = r["value"]

        # 3. 归一化 schema（分组 + 字段元数据 + 当前值/覆盖），前端 schema 驱动渲染
        overrides = {r["key"]: r["value"] for r in (rows or [])}
        schema = build_system_config_schema(overrides=overrides)

        return jsonify({"ok": True, "config": result, "schema": schema})
    except Exception as e:
        _log.error("[api/system-config GET] 异常: %s", e, exc_info=True)
        return (
            jsonify({"ok": False, "error": "读取系统配置失败: %s" % str(e)[:200]}),
            500,
        )
    finally:
        if conn:
            _release_sys_conn(conn)


@system_cfg_bp.route("/api/system-config", methods=["POST"])
def api_post_system_config():
    """批量更新系统级配置。body: {"config": {"key": "value", ...}}"""
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("system.settings")
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    body = request.get_json(silent=True) or {}
    config = body.get("config") or body
    if not isinstance(config, dict) or not config:
        return jsonify({"ok": False, "error": "请求体必须包含 config 字典"}), 400
    conn = None
    try:
        conn = _get_sys_conn()
        updated = 0
        for k, v in config.items():
            # UPSERT: 存在则更新，不存在则插入
            existing = conn.execute(
                "SELECT `value` FROM system_config WHERE `key`=%s", (k,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE system_config SET `value`=%s WHERE `key`=%s", (str(v), k)
                )
            else:
                conn.execute(
                    "INSERT INTO system_config (`key`, `value`) VALUES (%s, %s)",
                    (k, str(v)),
                )
            updated += 1
        conn.commit()
        _log.info("[api/system-config POST] 更新 %d 项配置", updated)
        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        _log.error("[api/system-config POST] 异常: %s", e, exc_info=True)
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("conn.rollback 失败: %s", _e, exc_info=True)
        return (
            jsonify({"ok": False, "error": "保存系统配置失败: %s" % str(e)[:200]}),
            500,
        )
    finally:
        if conn:
            _release_sys_conn(conn)
