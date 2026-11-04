# -*- coding: utf-8 -*-
"""DeepSeek + OpenAI Provider（OpenAI 兼容格式 + 各自计费）— 从 ai_providers.py 拆出，零业务逻辑变更。"""

from .openai_compat import _OpenAICompatProvider


class DeepSeekProvider(_OpenAICompatProvider):
    """DeepSeek API（OpenAI 兼容格式）。"""

    @property
    def source(self) -> str:
        return "deepseek"

    _PRICING = {
        "deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek-reasoner": {"input": 0.14, "output": 0.28},
        "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
        "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
        "_default": {"input": 0.435, "output": 0.87},
    }

    def _estimate_cost(self, usage: dict) -> float:
        pricing = self._PRICING.get(self._model, self._PRICING["_default"])
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cache_hit = usage.get("prompt_cache_hit_tokens", 0)
        cache_miss = usage.get("prompt_cache_miss_tokens", 0) or prompt_tokens
        discount = 0.1
        input_cost = (cache_miss / 1_000_000) * pricing["input"] + (
            cache_hit / 1_000_000
        ) * pricing["input"] * discount
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost


class OpenAIProvider(_OpenAICompatProvider):
    """OpenAI 原生 API（与 DeepSeek 格式相同，base URL / 定价不同）。"""

    @property
    def source(self) -> str:
        return "openai"

    _PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
        "o1": {"input": 15.00, "output": 60.00},
        "o3-mini": {"input": 1.10, "output": 4.40},
        "o4-mini": {"input": 1.10, "output": 4.40},
        "_default": {"input": 2.50, "output": 10.00},
    }

    def _estimate_cost(self, usage: dict) -> float:
        pricing = self._PRICING.get(self._model, self._PRICING["_default"])
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        return (prompt_tokens / 1_000_000) * pricing["input"] + (
            completion_tokens / 1_000_000
        ) * pricing["output"]
