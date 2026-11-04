# -*- coding: utf-8 -*-
"""
project_config 写入路由: api_save_config
"""

import json as _json
from src.core.guarded import guard
import logging
from flask import jsonify

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from src.backend.helpers import _user_can_access_project
from src.backend.pydantic_schemas import validate_body, ConfigUpdateSchema
from .constants import (
    _TYPE_BOOL_KEYS,
    _TYPE_INT_KEYS,
    _TYPE_FLOAT_KEYS,
    _VALID_CONFIG_KEYS,
    _coerce_config_value,
    _rebuild_valid_keys_cache,
)


def api_save_config():
    # ★ 强制重新加载 constants 模块（避免 Flask 进程缓存旧版本）
    import importlib
    from . import constants as _const_mod

    importlib.reload(_const_mod)
    # ★ reload 会创建新的模块级对象，必须重新绑定本地导入名
    #    否则 _VALID_CONFIG_KEYS / _rebuild_valid_keys_cache 等仍指向旧对象
    _rebuild_valid_keys_cache = _const_mod._rebuild_valid_keys_cache
    _VALID_CONFIG_KEYS = _const_mod._VALID_CONFIG_KEYS
    _TYPE_BOOL_KEYS = _const_mod._TYPE_BOOL_KEYS
    _TYPE_INT_KEYS = _const_mod._TYPE_INT_KEYS
    _TYPE_FLOAT_KEYS = _const_mod._TYPE_FLOAT_KEYS
    _coerce_config_value = _const_mod._coerce_config_value

    try:
        data, schema_err = validate_body(ConfigUpdateSchema)
        if schema_err:
            return schema_err
        proj = data.get("project") or _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        # ★ 字段类型校验前置：先校验 fields 结构/类型（400），再做权限校验（403）
        #    确保输入校验优先于权限校验，非法字段请求直接返回 400
        fields = data.get("fields", {})
        if not fields:
            return jsonify({"ok": False, "error": "没有配置项"}), 400

        if not isinstance(fields, dict):
            return jsonify({"ok": False, "error": "fields 必须是对象"}), 400
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        _ALLOWED_SIMPLE_TYPES = (str, int, float, bool, type(None))
        _bad_keys = []
        for k, v in fields.items():
            if not isinstance(k, str):
                _bad_keys.append(repr(k))
                continue
            if isinstance(v, (dict, list)):
                _bad_keys.append(k)
            elif not isinstance(v, _ALLOWED_SIMPLE_TYPES):
                _bad_keys.append(k)
        if _bad_keys:
            _log.warning(
                "[SECURITY] api_save_config 拒绝非法字段类型: %s (user=%s, project=%s)",
                _bad_keys,
                uid,
                proj,
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "字段值必须为字符串/数字/布尔/空值，不允许对象或数组嵌套",
                        "rejected_keys": _bad_keys,
                    }
                ),
                400,
            )
        if uid and not _user_can_access_project(uid, proj):
            return jsonify({"ok": False, "error": "无权访问项目 %s" % proj}), 403
        # ★ 权限校验：仅项目管理员（project.config.edit）可保存配置，项目成员只读
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("project.config.edit", proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), 403

        try:
            from src.core.config_loader import config as cfg

            cfg.reload(proj, force=True)
            current = {}
            from src.core.config_loader import ConfigLoader

            ymap = ConfigLoader._YAML_KEY_MAP
            for k in fields:
                if not isinstance(k, str):
                    continue
                v = cfg._get(k)
                if v is not None:
                    current[k] = v
            for k in fields:
                if not isinstance(k, str):
                    continue
                long_key = ymap.get(k)
                if long_key and long_key not in current:
                    lv = cfg._get(long_key)
                    if lv is not None:
                        current[long_key] = lv
                short_key = next((sk for sk, lk in ymap.items() if lk == k), None)
                if short_key and short_key not in current:
                    sv = cfg._get(short_key)
                    if sv is not None:
                        current[short_key] = sv
            _rebuild_valid_keys_cache()
        except Exception as e:
            _log.error("[api_save_config] ConfigLoader 加载失败: %s", e, exc_info=True)
            current = {}

        # ★ 热修复：直接在运行时构建完整白名单（避免 Flask 进程缓存旧模块）
        _ALL_VALID_KEYS = set(_VALID_CONFIG_KEYS or [])
        _ALL_VALID_KEYS.update(
            {
                # 基础字段兜底（不含凭证字段——凭证走 /api/user-credentials → user_credentials_v2）
                "CONFLUENCE_URL",
                "CONFLUENCE_MODE",
                "FIGMA_URL",
                "DB_SANDBOX_MODE",
                "TARGET_URL",
                "TARGET_DB_HOST",
                "TARGET_DB_PORT",
                "TARGET_DB_NAME",
                "TARGET_DB_TYPE",
                "TARGET_DB_FILE",
                "TARGET_DB_ENABLED",
                "TARGET_DB_AUTO_MODE",
                "API_DOCS_URL",
                "PROJECT_VERSION",
                "AI_PROVIDER",
                "AI_MODEL",
                "AI_TIMEOUT",
                "AI_OPENAI_BASE_URL",
                "AI_ANTHROPIC_BASE_URL",
                "AI_DEEPSEEK_BASE_URL",
                "AI_NVIDIA_BASE_URL",
                "AI_VERIFICATION_MODEL",
                "DEEPSEEK_MODEL",
                "DEEPSEEK_API_KEY",
                "DEEPSEEK_BASE_URL",
                "DEEPSEEK_TIMEOUT",
                "OPENAI_BASE_URL",
                "ANTHROPIC_BASE_URL",
                "NVIDIA_BASE_URL",
                "NVIDIA_API_KEY",
                "NVIDIA_MODEL",
                "OLLAMA_ENABLED",
                "OLLAMA_API_BASE",
                "OLLAMA_MODEL",
                "PLAYWRIGHT_HEADLESS",
                "PLAYWRIGHT_TIMEOUT",
                "PLAYWRIGHT_VIEWPORT_WIDTH",
                "PLAYWRIGHT_VIEWPORT_HEIGHT",
                "PLAYWRIGHT_SCREENSHOT_ON_FAILURE",
                "PLAYWRIGHT_SCREENSHOT_DIR",
                "VISION_MODEL_ENABLED",
                "VISION_MODEL",
                "SESSION_AWARE_LOGIN",
                "SESSION_STATE_CACHE_SECONDS",
                "SESSION_SMART_STEP_STRIP_LOGIN",
                "SESSION_SMART_STEP_EXPAND_IMPORT",
                "SESSION_SYSTEM_KNOWLEDGE_INJECT_PROMPT",
                "SESSION_DATA_SCHEMA_FILE",
                "SMART_STEP_STRIP_LOGIN",
                "SMART_STEP_EXPAND_IMPORT",
                "SYSTEM_KNOWLEDGE_INJECT_PROMPT",
                "DATA_SCHEMA_FILE",
                "RAG_VECTOR_SEARCH_THRESHOLD",
                "RAG_HYBRID_BM25_WEIGHT",
                "RAG_RRF_K",
                "RAG_SIMILARITY_METRIC",
                "FLAKY_DETECTION_ENABLED",
                "SCHEMA_VALIDATION_ENABLED",
                "REQUIREMENTS_EXCEL_PATH",
                "TEST_CASES_EXCEL_PATH",
                "EXCEL_REQUIREMENTS_PATH",
                "EXCEL_TEST_CASES_PATH",
                "REQUIREMENT_REVIEW_GATE",
                "STORAGE_ENGINE",
                "STORAGE_MYSQL_HOST",
                "STORAGE_MYSQL_PORT",
                "STORAGE_MYSQL_DATABASE",
                "STORAGE_MYSQL_USER",
                "STORAGE_MYSQL_PASSWORD",
                "STORAGE_MYSQL_CHARSET",
            }
        )
        _all_valid = _ALL_VALID_KEYS

        rejected_unknown = []
        for k in fields:
            if not isinstance(k, str):
                continue
            if k in _all_valid or _all_valid and False:
                pass
            if not _all_valid or k in _all_valid:
                continue
            rejected_unknown.append(k)
        if rejected_unknown:
            _log.warning(
                "[SECURITY] api_save_config 拒绝非法字段名（可能拼写错误）: %s",
                rejected_unknown,
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "存在未识别的配置字段（可能拼写错误）",
                        "rejected_keys": rejected_unknown,
                    }
                ),
                400,
            )

        current_str = {}
        for k, v in current.items():
            current_str[k] = (
                str(v)
                if not isinstance(v, (list, tuple))
                else ", ".join(str(x) for x in v)
            )

        _PORT_KEYS = {"TARGET_DB_PORT", "STORAGE_MYSQL_PORT"}
        safe_fields = {}
        skipped = []
        for k, v in fields.items():
            v_str = str(v).strip()
            # 端口号校验：必须为 1-65535 之间的整数
            if k in _PORT_KEYS and v_str:
                try:
                    port_val = int(v_str)
                    if not (1 <= port_val <= 65535):
                        return (
                            jsonify(
                                {"ok": False, "error": f"{k} 必须是 1-65535 之间的整数"}
                            ),
                            400,
                        )
                except (ValueError, TypeError):
                    return jsonify({"ok": False, "error": f"{k} 必须是有效整数"}), 400
            is_type_key = (
                k in _TYPE_BOOL_KEYS or k in _TYPE_INT_KEYS or k in _TYPE_FLOAT_KEYS
            )
            if not is_type_key and v_str == "" and current_str.get(k, ""):
                skipped.append(k)
                continue
            safe_fields[k] = _coerce_config_value(k, v_str, current.get(k))
        if skipped:
            _log.info(
                "[Config Save] 跳过空值覆写 (字符串字段，%s): %s",
                proj,
                ", ".join(skipped),
            )

        if not safe_fields:
            return jsonify({"ok": True, "updated": [], "skipped": skipped})

        _save_ok = False
        try:
            from src.users.manager import _conn

            conn = _conn()
            new_json = _json.dumps(safe_fields, ensure_ascii=False)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO project_configs (project, config_json) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE "
                        "config_json = JSON_MERGE_PATCH(COALESCE(config_json, '{}'), %s)",
                        (proj, new_json, new_json),
                    )
                conn.commit()
                _save_ok = True
                _log.info(
                    f"[Config Save] 已原子写入 project_configs 表 ({proj}): "
                    f"{', '.join(safe_fields.keys())}"
                )
            except Exception as merge_err:
                _log.warning(
                    "[Config Save] JSON_MERGE_PATCH 不可用 (%s)，回退到 FOR UPDATE 事务",
                    merge_err,
                )
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT config_json FROM project_configs "
                            "WHERE project=%s FOR UPDATE",
                            (proj,),
                        )
                        row = cur.fetchone()
                        existing = _json.loads(row[0]) if row and row[0] else {}
                        existing.update(safe_fields)
                        merged_json = _json.dumps(existing, ensure_ascii=False)
                        cur.execute(
                            "INSERT INTO project_configs (project, config_json) VALUES (%s, %s) "
                            "ON DUPLICATE KEY UPDATE config_json=%s",
                            (proj, merged_json, merged_json),
                        )
                    conn.commit()
                    _save_ok = True
                    _log.info(
                        f"[Config Save] FOR UPDATE 事务已写入 ({proj}): "
                        f"{', '.join(safe_fields.keys())}"
                    )
                except Exception as inner_err:
                    conn.rollback()
                    raise inner_err
            finally:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("ai + conn.close + close(): %s", _e, exc_info=True)
        except Exception as e:
            _log.error("[Config Save] MySQL 写入失败: %s", e, exc_info=True)
            _save_ok = False

        if not _save_ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "配置写入失败（MySQL 不可用）",
                        "failed_fields": list(safe_fields.keys()),
                    }
                ),
                500,
            )

        updated = set(safe_fields.keys())

        reload_errors = []
        try:
            cfg._config_data.update(safe_fields)
            cfg.reload(proj)  # ★ 保存后重新加载配置，确保数据库写入立即生效
            _rebuild_valid_keys_cache()
            cfg._last_loaded_project = proj
        except Exception as e:
            reload_errors.append("ConfigLoader: " + str(e)[:200])
            _log.error("[Config Save] ConfigLoader 内存更新失败: %s", e, exc_info=True)

        try:
            from src.extensions.db.db_core import db_executor

            db_executor.reload_config()
        except Exception as e:
            reload_errors.append("DbExecutor: " + str(e)[:200])
            _log.error(
                "[Config Save] db_executor.reload_config 失败: %s", e, exc_info=True
            )

        from src.users.manager import (
            log_action,
            record_config_change,
            _conn as _hist_conn_fn,
        )

        with guard("api_save_config: log_action( uid, 'config-save', scop", log=_log, level=logging.WARNING):
            log_action(
                uid,
                "config-save",
                scope_type="project",
                scope_id=proj,
                detail="updated=" + ",".join(updated),
            )
        _hist_conn = _hist_conn_fn()
        try:
            for k in updated:
                old = current_str.get(k, "")
                new = str(safe_fields.get(k, ""))
                try:
                    record_config_change(
                        uid or "system",
                        k,
                        old,
                        new,
                        scope_type="project",
                        scope_id=proj,
                        conn=_hist_conn,
                    )
                except Exception as e:
                    _log.warning(
                        "[Config Save] record_config_change 失败 (%s): %s", k, e
                    )
            _hist_conn.commit()
        finally:
            _hist_conn.close()

        if "PROJECT_VERSION" in safe_fields:
            try:
                ver_label = str(safe_fields["PROJECT_VERSION"]).strip()
                if ver_label:
                    from src.users.manager import (
                        _conn as _vc,
                        create_project_version,
                        activate_project_version,
                    )

                    _vc_conn = None
                    version_id = None
                    _vc_conn = _vc()
                    cur = _vc_conn.execute(
                        "SELECT id FROM project_versions WHERE project=%s AND version_label=%s",
                        (proj, ver_label),
                    )
                    row = cur.fetchone()
                    if row:
                        version_id = row["id"]
                    else:
                        version_id = create_project_version(
                            proj, ver_label, "UI 配置同步", uid or "system", "draft"
                        )
                    if version_id:
                        activate_project_version(proj, int(version_id))
            except Exception as e:
                _log.warning("[Config Save] project_versions 同步失败: %s", e)
            finally:
                try:
                    if "_vc_conn" in locals() and _vc_conn:
                        _vc_conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

        if reload_errors:
            _log.warning(
                "[Config Save] 配置已保存但部分模块重载失败: %s",
                "; ".join(reload_errors),
            )
            return jsonify(
                {
                    "ok": True,
                    "updated": list(updated),
                    "skipped": skipped,
                    "warning": "配置已写入数据库，但部分模块重载失败，请刷新页面",
                    "reload_errors": reload_errors,
                }
            )

        return jsonify({"ok": True, "updated": list(updated), "skipped": skipped})
    except Exception as e:
        _log.error("[api_save_config] 未处理异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器异常: " + str(e)[:200]}), 500
