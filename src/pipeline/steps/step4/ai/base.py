# -*- coding: utf-8 -*-
"""BrowserAi 基础 mixin

职责：
- __init__ / _model_supports_tools
- 类属性配置（MAX_AI_ROUNDS / MAX_DOM_ELEMENTS / VIEWPORT_FIRST_ROUNDS）
- 标准能力门面委托（_try_search_or_filter / _try_delete_record / _try_export_data / _try_create_business_record）
- _tool_results_to_actions 门面委托
"""

import logging
from src.core.guarded import guard

from src.core.config_loader import config
from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)


class _BrowserAiBaseMixin:
    """BrowserAi 基础能力。"""

    @property
    def MAX_AI_ROUNDS(self):
        return int(_cfg("browser_max_ai_rounds", 8))

    @property
    def MAX_DOM_ELEMENTS(self):
        return int(_cfg("browser_max_dom_elements", 120))

    VIEWPORT_FIRST_ROUNDS = 3

    def __init__(self, engine):
        self._engine = engine
        self._nav_context = ""  # ★ 2026-06-30: nav_map 已删除，保留属性兼容
        self._last_valid_ai_ids: set = set()
        self._last_guard_error: str = ""
        self.ALLOWED_ACTIONS = self._load_allowed_actions()
        # ★ 用户配置 > config.yaml > 默认值（property）
        _cfg_rounds = int(_cfg("browser_max_ai_rounds", 0))
        if _cfg_rounds > 0:
            self.MAX_AI_ROUNDS = _cfg_rounds
        else:
            _cfg_rounds = getattr(config, "max_ai_rounds", None)
            if (
                _cfg_rounds is not None
                and isinstance(_cfg_rounds, int)
                and _cfg_rounds > 0
            ):
                self.MAX_AI_ROUNDS = _cfg_rounds

    def _load_allowed_actions(self):
        with guard("_load_allowed_actions: from src.pipeline.utils.action_libra", log=_log, level=logging.WARNING):
            from src.pipeline.utils.action_library_store import action_library_store

            allowed = action_library_store.load_allowed_action_ids()
            if allowed:
                from src.pipeline.steps.step4.browser_ai import _DEFAULT_ALLOWED_ACTIONS

                return set(allowed) | set(_DEFAULT_ALLOWED_ACTIONS)
        from src.pipeline.steps.step4.browser_ai import _DEFAULT_ALLOWED_ACTIONS

        return set(_DEFAULT_ALLOWED_ACTIONS)

    def _model_supports_tools(self) -> bool:
        try:
            from src.ai.ai_client import ai_brain

            provider = getattr(ai_brain, "_provider", None)
            if provider is not None and hasattr(provider, "supports"):
                return provider.supports("tools")
        except Exception as _e:
            _log.debug("[base] _model_supports_tools 异常: %s", _e, exc_info=True)
        return False

    # --- 标准能力门面委托 ---

    async def _try_search_or_filter(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_search_or_filter,
        )

        return await try_search_or_filter(self._engine, goal)

    async def _try_delete_record(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_delete_record

        return await try_delete_record(self._engine, goal)

    async def _try_export_data(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_export_data

        return await try_export_data(self._engine, goal)

    async def _try_create_business_record(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_create_business_record,
        )

        return await try_create_business_record(self._engine, goal)

    async def _try_login_with_password(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_login_with_password,
        )

        return await try_login_with_password(self._engine, goal)

    async def _try_form_fill_and_submit(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_form_fill_and_submit,
        )

        return await try_form_fill_and_submit(self._engine, goal)

    async def _try_data_query(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_data_query

        return await try_data_query(self._engine, goal)

    async def _try_approval_flow(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_approval_flow

        return await try_approval_flow(self._engine, goal)

    async def _try_list_batch_action(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_list_batch_action,
        )

        return await try_list_batch_action(self._engine, goal)

    async def _try_navigate_menu(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_navigate_menu

        return await try_navigate_menu(self._engine, goal)

    async def _try_pagination_control(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import (
            try_pagination_control,
        )

        return await try_pagination_control(self._engine, goal)

    async def _try_table_export(self, goal: str) -> bool:
        from src.pipeline.steps.step4.browser_ai_capabilities import try_table_export

        return await try_table_export(self._engine, goal)

    def _tool_results_to_actions(self, tool_calls: list[dict], results) -> list[dict]:
        from src.pipeline.steps.step4.browser_ai_tools import (
            tool_results_to_actions as _fn,
        )

        return _fn(tool_calls, results)
