# -*- coding: utf-8 -*-
"""
模型定价查询 mixin

职责：
- get_model_pricing / refresh_pricing
"""


class _ConfigPricingMixin:
    """模型定价查询。"""

    def get_model_pricing(self, model_name: str = None) -> dict:
        from src.ai.pricing_fetcher import pricing_fetcher

        name = model_name or self.deepseek_model
        return pricing_fetcher.get_price(name)

    def refresh_pricing(self) -> bool:
        from src.ai.pricing_fetcher import pricing_fetcher

        return pricing_fetcher.refresh()
