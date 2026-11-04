# -*- coding: utf-8 -*-
"""BrowserAi 响应校验 mixin

职责：
- _parse_ai_response — 解析 AI 返回的 JSON
- _validate_actions — 校验动作合法性（门面委托到 browser_ai_validator）
- _validate_ai_response — 统一校验入口
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class _BrowserAiValidatorMixin:
    """AI 响应解析与校验能力。"""

    def _parse_ai_response(self, raw: str) -> list[dict[str, Any]]:
        """解析 AI 返回的 JSON 操作序列。"""
        if not raw or raw == "null":
            return []
        try:
            if raw.startswith("```"):
                raw = raw.strip("`").strip()
                if raw.lower().startswith("json"):
                    raw = raw[4:].strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("actions", [])
            return []
        except json.JSONDecodeError:
            _log.warning("    [AI] JSON 解析失败，尝试提取 JSON 数组...")
            try:
                start = raw.find("[")
                end = raw.rfind("]")
                if start >= 0 and end > start:
                    data = json.loads(raw[start : end + 1])
                    return data if isinstance(data, list) else []
            except Exception as _e:
                _log.debug("[validator] _parse_ai_response 异常: %s", _e, exc_info=True)
            return []
        except Exception as e:
            _log.warning("    [AI] 解析异常: %s", str(e)[:80])
            return []

    def _validate_actions(
        self, actions: list[dict[str, Any]], state: list[dict]
    ) -> str:
        """校验动作合法性（门面委托）。"""
        from src.pipeline.steps.step4.browser_ai_validator import validate_actions

        return validate_actions(actions, state, allowed_actions=self.ALLOWED_ACTIONS)

    def _validate_ai_response(
        self, parsed: list[dict[str, Any]], compact_state: list[dict]
    ) -> str:
        """统一校验入口：由 _validate_actions 硬规则校验替代原 CoVe 自查。"""
        return self._validate_actions(parsed, compact_state)
