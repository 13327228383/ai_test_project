# -*- coding: utf-8 -*-
"""
AiBrain 统计 Mixin
==================
提供统计信息查询与重置能力。
"""

from typing import Any


class _StatsMixin:
    """Token 计费统计相关属性与方法。"""

    @property
    def total_calls(self) -> int:
        return self._stats.total_calls

    @property
    def total_cost_usd(self) -> float:
        return self._stats.total_cost_usd

    def get_stats(self) -> dict[str, Any]:
        """返回累计统计信息。"""
        return self._stats.get_stats(self._model)

    def reset_stats(self):
        """重置累计统计信息。"""
        self._stats.reset()
