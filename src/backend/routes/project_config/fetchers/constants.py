# -*- coding: utf-8 -*-

import logging

_log = logging.getLogger(__name__)
"""

project_config 共享常量 + 辅助函数

"""


# ── 类型键集合 ──

_TYPE_BOOL_KEYS = {
    "TARGET_DB_ENABLED",
    "TARGET_DB_AUTO_MODE",
    "PLAYWRIGHT_HEADLESS",
    "PLAYWRIGHT_SCREENSHOT_ON_FAILURE",
    "SESSION_AWARE_LOGIN",
    "SMART_STEP_STRIP_LOGIN",
    "SMART_STEP_EXPAND_IMPORT",
    "SYSTEM_KNOWLEDGE_INJECT_PROMPT",
    "REQUIREMENT_REVIEW_GATE",
    "VISION_MODEL_ENABLED",
    "FLAKY_DETECTION_ENABLED",
    "SCHEMA_VALIDATION_ENABLED",
    "COVE_ENABLED",
    "DB_ENABLED",
    "DB_AUTO_MODE",
}


_TYPE_INT_KEYS = {
    "DEEPSEEK_TIMEOUT",
    "PLAYWRIGHT_TIMEOUT",
    "PLAYWRIGHT_VIEWPORT_WIDTH",
    "PLAYWRIGHT_VIEWPORT_HEIGHT",
    "TARGET_DB_PORT",
    "DB_PORT",
    "SESSION_STATE_CACHE_SECONDS",
    "FIGMA_PARSE_MAX_DEPTH",
    "FIGMA_PARSE_MAX_PENETRATION",
    "RAG_RRF_K",
}


_TYPE_FLOAT_KEYS = {
    "RAG_VECTOR_SEARCH_THRESHOLD",
    "RAG_HYBRID_BM25_WEIGHT",
}


# ── 已知字段默认值（仅在 cfg._get(k) 返回 None 时用作回退）──
# ★ 这些默认值定义在 smart_session.py / pipeline_config.py 的属性里，
#    不在 config.yaml 中，因此 cfg._get(k) 无法返回。
#    Fix55a 通过此字典为前端展示提供兜底默认值。
_KNOWN_DEFAULTS = {
    "FIGMA_PARSE_MAX_DEPTH": 6,
    "FIGMA_PARSE_MAX_PENETRATION": 24,
    "RAG_VECTOR_SEARCH_THRESHOLD": 0.3,
}


_VALID_CONFIG_KEYS = set()


_STATIC_CONFIG_KEYS = {
    "TARGET_DB_ENABLED",
    "TARGET_DB_AUTO_MODE",
    "PLAYWRIGHT_HEADLESS",
    "PLAYWRIGHT_SCREENSHOT_ON_FAILURE",
    "SESSION_AWARE_LOGIN",
    "SMART_STEP_STRIP_LOGIN",
    "SMART_STEP_EXPAND_IMPORT",
    "SYSTEM_KNOWLEDGE_INJECT_PROMPT",
    "REQUIREMENT_REVIEW_GATE",
    "VISION_MODEL_ENABLED",
    "FLAKY_DETECTION_ENABLED",
    "SCHEMA_VALIDATION_ENABLED",
    "DEEPSEEK_TIMEOUT",
    "PLAYWRIGHT_TIMEOUT",
    "PLAYWRIGHT_VIEWPORT_WIDTH",
    "PLAYWRIGHT_VIEWPORT_HEIGHT",
    "TARGET_DB_PORT",
    "SESSION_STATE_CACHE_SECONDS",
    "FIGMA_PARSE_MAX_DEPTH",
    "FIGMA_PARSE_MAX_PENETRATION",
    "RAG_RRF_K",
    "RAG_VECTOR_SEARCH_THRESHOLD",
    "RAG_HYBRID_BM25_WEIGHT",
    "AI_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "VERIFICATION_MODEL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "CONFLUENCE_URL",
    "CONFLUENCE_MODE",
    "FIGMA_URL",
    "TARGET_URL",
    "API_DOCS_URL",
    "PLAYWRIGHT_SCREENSHOT_DIR",
    "REQUIREMENTS_EXCEL_PATH",
    "TEST_CASES_EXCEL_PATH",
    "DATA_SCHEMA_FILE",
    "TARGET_DB_TYPE",
    "TARGET_DB_HOST",
    "TARGET_DB_PORT",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_ENABLED",
    "DB_USER",
    "DB_PASSWORD",
    "TARGET_DB_NAME",
    "TARGET_DB_FILE",
    "TARGET_DB_AUTO_MODE",
    "TARGET_DB_ENABLED",
    "DB_SANDBOX_MODE",
    "COVE_ENABLED",
    "VISION_MODEL",
    "PROJECT_VERSION",
    "RAG_SIMILARITY_METRIC",
    "STORAGE_ENGINE",
    "STORAGE_MYSQL_HOST",
    "STORAGE_MYSQL_PORT",
    "STORAGE_MYSQL_USER",
    "STORAGE_MYSQL_PASSWORD",
    "STORAGE_MYSQL_DATABASE",
    "STORAGE_MYSQL_CHARSET",
}


def _rebuild_valid_keys_cache():

    global _VALID_CONFIG_KEYS

    # ★ 防止初始 None 导致 AttributeError

    if _VALID_CONFIG_KEYS is None:

        _VALID_CONFIG_KEYS = set()

    try:

        fields = set(_STATIC_CONFIG_KEYS)

        try:

            from src.core.config_loader import ConfigLoader

            fields.update(ConfigLoader._YAML_KEY_MAP.keys())

            fields.update(ConfigLoader._YAML_KEY_MAP.values())

        except Exception as _e:

            _log.debug("import + load + update: %s", _e, exc_info=True)

        _VALID_CONFIG_KEYS.clear()

        _VALID_CONFIG_KEYS.update(fields)

    except Exception:

        _VALID_CONFIG_KEYS.clear()

        _VALID_CONFIG_KEYS.update(_STATIC_CONFIG_KEYS)


def _coerce_config_value(key: str, new_val, old_val=None):

    try:

        if new_val is None:

            return ""

        if not isinstance(new_val, str):

            try:

                new_val = str(new_val)

            except Exception:

                return ""

        new_val = new_val.strip()

        if not new_val:

            if key in _TYPE_BOOL_KEYS:

                return False

            if key in _TYPE_INT_KEYS:

                return 0

            if key in _TYPE_FLOAT_KEYS:

                return 0.0

            return ""

        if old_val is not None and not isinstance(old_val, str):

            if isinstance(old_val, bool):

                return new_val.lower() in ("true", "1", "yes", "on", "y", "t")

            if isinstance(old_val, int):

                try:

                    return int(float(new_val))

                except (ValueError, TypeError):

                    return str(new_val)

            if isinstance(old_val, float):

                try:

                    return float(new_val)

                except (ValueError, TypeError):

                    return str(new_val)

        if key in _TYPE_BOOL_KEYS:

            try:

                return new_val.lower() in ("true", "1", "yes", "on", "y", "t")

            except AttributeError:

                return bool(new_val)

        if key in _TYPE_INT_KEYS:

            try:

                return int(float(new_val))

            except (ValueError, TypeError):

                return str(new_val)

        if key in _TYPE_FLOAT_KEYS:

            try:

                return float(new_val)

            except (ValueError, TypeError):

                return str(new_val)

        return new_val

    except Exception:

        try:

            return str(new_val)

        except Exception:

            return ""
