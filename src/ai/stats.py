# -*- coding: utf-8 -*-
"""
统计与计费模块
==============
封装 AiBrain 的 Token 用量统计与费用计算逻辑。

设计原则：依赖 config_loader 获取定价，其余无状态。
"""

from typing import Any

from src.core.config_loader import config


class TokenStats:
    """累计 Token 用量与费用统计。"""

    def __init__(self) -> None:
        self._total_calls = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_cost_usd = 0.0

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    def record(self, usage: dict, cost: float) -> None:
        """记录一次调用的用量与费用。"""
        if usage:
            self._total_calls += 1
            self._total_prompt_tokens += usage.get("prompt_tokens", 0)
            self._total_completion_tokens += usage.get("completion_tokens", 0)
            self._total_cost_usd += cost

    def get_stats(self, model: str) -> dict[str, Any]:
        """返回累计统计信息。"""
        exchange_rate = getattr(config, "usd_cny_rate", 7.2)
        total_cost_cny = round(self._total_cost_usd * exchange_rate, 4)
        return {
            "total_calls": self._total_calls,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_cost_usd": round(self._total_cost_usd, 6),
            "total_cost_cny": total_cost_cny,
            "model": model,
            "pricing": config.get_model_pricing(model),
            "exchange_rate": exchange_rate,
        }


def calc_cost(model: str, usage: dict) -> float:
    """
    根据模型定价和 Token 用量计算费用（美元）。

    DeepSeek API 的 usage 对象结构：
    {
        "prompt_tokens": 1234,
        "completion_tokens": 567,
        "prompt_cache_hit_tokens": 0,      # 缓存命中（费用折扣）
        "prompt_cache_miss_tokens": 0,     # 缓存未命中
        "completion_tokens_details": {         # 仅 reasoning 模型
            "reasoning_tokens": 89
        }
    }
    """
    pricing = config.get_model_pricing(model)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # 缓存命中 token 享受折扣（折扣率从定价数据获取，默认 0.1 = 1/10 计费）
    # ★ 修复：prompt_cache_miss_tokens=0 是合法值（100%命中），不能用 or 短路
    cache_miss_raw = usage.get("prompt_cache_miss_tokens")
    cache_miss = cache_miss_raw if cache_miss_raw is not None else prompt_tokens
    cache_hit = usage.get("prompt_cache_hit_tokens", 0)
    cache_discount = pricing.get("cache_hit_discount", 0.1)

    input_cost = (cache_miss / 1_000_000) * pricing["input"] + (
        cache_hit / 1_000_000
    ) * pricing["input"] * cache_discount

    # completion_tokens 已包含 reasoning_tokens（DeepSeek API 行为）
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]

    return input_cost + output_cost
