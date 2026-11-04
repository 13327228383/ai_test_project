# -*- coding: utf-8 -*-
"""浏览器导航动作 — go_back_element + go_forward_element。

用于浏览器后退/前进导航，覆盖面包屑、返回按钮、历史导航等场景。
"""

import logging

_log = logging.getLogger(__name__)


class GoBackMixin:
    """浏览器后退/前进动作。"""

    async def go_back_element(self, wait_until: str = "load") -> bool:
        """浏览器后退一页。"""
        if not self._page:
            return False
        try:
            response = await self._page.go_back(wait_until=wait_until, timeout=10000)
            # go_back 在无历史记录时返回 None，不算失败
            return True
        except Exception as e:
            _log.warning("[go_back] 后退失败: %s", e)
            return False

    async def go_forward_element(self, wait_until: str = "load") -> bool:
        """浏览器前进一页。"""
        if not self._page:
            return False
        try:
            await self._page.go_forward(wait_until=wait_until, timeout=10000)
            return True
        except Exception as e:
            _log.warning("[go_forward] 前进失败: %s", e)
            return False
