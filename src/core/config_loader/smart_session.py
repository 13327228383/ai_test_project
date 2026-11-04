# -*- coding: utf-8 -*-

import logging

_log = logging.getLogger(__name__)
"""

智能会话、扩展功能与 RAG 配置 mixin



职责：

- 会话感知 / 智能步骤 / 审核闸门

- Flaky / Schema / Figma 解析深度 / DB Sandbox

- 视觉模型 / 智能导入 / 系统知识注入 / data_schema_file

- Excel 路径 / RAG 参数

"""


class _ConfigSmartSessionMixin:
    """智能会话与扩展功能配置。"""

    @property
    def session_aware_login(self) -> bool:

        return self._get("SESSION_AWARE_LOGIN", True)

    @property
    def session_state_cache_seconds(self) -> int:

        return self._get("SESSION_STATE_CACHE_SECONDS", 120)

    @property
    def smart_step_strip_login(self) -> bool:

        return self._get("SMART_STEP_STRIP_LOGIN", True)

    @property
    def requirement_review_gate(self) -> bool:

        return self._get("REQUIREMENT_REVIEW_GATE", True)

    @property
    def flaky_detection_enabled(self) -> bool:

        return self._get("FLAKY_DETECTION_ENABLED", True)

    @property
    def schema_validation_enabled(self) -> bool:

        return self._get("SCHEMA_VALIDATION_ENABLED", True)

    @property
    def figma_parse_max_depth(self) -> int:

        return self._get("FIGMA_PARSE_MAX_DEPTH", 6)

    @property
    def figma_parse_max_penetration(self) -> int:

        return self._get("FIGMA_PARSE_MAX_PENETRATION", 24)

    @property
    def db_sandbox_mode(self) -> str:

        return self._get("DB_SANDBOX_MODE", "none")

    @property
    def vision_model(self) -> str:

        from src.core.config_session import get_current_user

        uid = get_current_user()

        # ★ CLI/流水线模式：无登录用户时回退 admin（与 ai_model/verification_model 一致）。
        #   此前缺此回退 → user_credentials 的 VISION_MODEL 读不到 → 云端视觉门控
        #   "模型未就绪"恒 SKIP，视觉维度在所有 CLI 实证路径恒 NO_DATA。
        if not uid:

            uid = "admin"

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_model = get_user_credential(uid, "VISION_MODEL", project="*")

                if user_model:

                    return user_model

            except Exception as _e:

                _log.debug("model + vision_model + import: %s", _e, exc_info=True)

        # 无独立视觉模型时回退主模型（多模态主模型可直接服务视觉；不可用则调用链
        # 自行降级 NO_DATA，不伪造通过）。
        return self._get("VISION_MODEL", "") or self.ai_model

    @property
    def vision_api_key(self) -> str:

        # ★ Fix: 视觉模型独立 API key — 读取 VISION_API_KEY，fallback 到主 ai_api_key（向后兼容）

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_key = get_user_credential(uid, "VISION_API_KEY", project="*")

                if user_key:

                    return user_key

            except Exception as _e:

                _log.debug("vision_api_key + import: %s", _e, exc_info=True)

        # fallback 到主 key（同 provider 场景安全，异构 provider 需配置独立 key）

        return self._get("VISION_API_KEY", "") or self.ai_api_key

    @property
    def vision_base_url(self) -> str:

        # ★ Fix: 视觉模型独立 base_url — 读取 VISION_BASE_URL，fallback 到主 ai_base_url

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_base = get_user_credential(uid, "VISION_BASE_URL", project="*")

                if user_base:

                    return user_base

            except Exception as _e:

                _log.debug("vision_base_url + import: %s", _e, exc_info=True)

        return self._get("VISION_BASE_URL", "") or self.ai_base_url

    @property
    def embed_model(self) -> str:

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_model = get_user_credential(uid, "EMBED_MODEL", project="*")

                if user_model:

                    return user_model

            except Exception as _e:

                _log.debug("model + embed_model + import: %s", _e, exc_info=True)

        return self._get("EMBED_MODEL", "")

    @property
    def vision_model_enabled(self) -> bool:

        from src.core.config_session import get_current_user

        uid = get_current_user()

        if uid:

            try:

                from src.users.manager import get_user_credential

                user_val = get_user_credential(uid, "VISION_MODEL_ENABLED", project="*")

                if user_val:

                    return user_val.lower() in ("1", "true", "yes")

            except Exception as _e:

                _log.debug(
                    "model + vision_model_enabled + import: %s", _e, exc_info=True
                )

        return self._get("VISION_MODEL_ENABLED", True)

    @property
    def vision_model_pricing(self) -> dict:

        # ★ 已删除硬编码兜底，全部从 API 实时拉取

        return self._get("VISION_MODEL_PRICING", {})

    @property
    def smart_step_expand_import(self) -> bool:

        return self._get("SMART_STEP_EXPAND_IMPORT", True)

    @property
    def system_knowledge_inject_prompt(self) -> bool:

        return self._get("SYSTEM_KNOWLEDGE_INJECT_PROMPT", True)

    @property
    def data_schema_file(self) -> str:

        return self._get("DATA_SCHEMA_FILE", "")

    @property
    def requirements_excel_path(self) -> str:

        return self._get("REQUIREMENTS_EXCEL_PATH", "")

    @property
    def test_cases_excel_path(self) -> str:

        return self._get("TEST_CASES_EXCEL_PATH", "")

    # ★ 深度合并：RAG 调参统一走 pipeline_config._read_config_value（单一来源），
    #   优先读「质量治理→流水线配置」的用户覆盖，回退 config.yaml 默认。
    def _pipe_val(self, key, default):
        from src.core.pipeline_config import _read_config_value

        return _read_config_value(key, default)

    @property
    def rag_similarity_metric(self) -> str:

        return self._pipe_val("RAG_SIMILARITY_METRIC", "auto")

    @property
    def rag_hybrid_bm25_weight(self) -> float:

        v = self._pipe_val("RAG_HYBRID_BM25_WEIGHT", None)

        if v is None:

            return None

        try:

            return float(v)

        except (ValueError, TypeError) as _e:

            _log.debug("[smart_session] float 转换失败(§5): %s", _e)

            return None

    @property
    def rag_vector_search_threshold(self) -> float:

        try:

            return float(self._get("RAG_VECTOR_SEARCH_THRESHOLD", 0.3))

        except (ValueError, TypeError):

            return 0.3

    @property
    def rag_rrf_k(self) -> int:

        try:

            return int(self._pipe_val("RAG_RRF_K", 60))

        except (ValueError, TypeError):

            return 60
