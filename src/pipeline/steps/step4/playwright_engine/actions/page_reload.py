# -*- coding: utf-8 -*-
"""页面刷新动作 — page_reload_element。

主动触发页面刷新，用于"操作后刷新验证数据持久化"等场景。
支持 wait_until 参数控制等待策略。
"""

import logging

_log = logging.getLogger(__name__)


class PageReloadMixin:
    """页面刷新动作。"""

    async def page_reload_element(
        self,
        wait_until: str = "load",
        timeout: int = 30000,
    ) -> bool:
        """刷新当前页面。

        wait_until: load(默认) / domcontentloaded / networkidle
        timeout: 等待超时（毫秒）。
        """
        if not self._page:
            return False
        try:
            await self._page.reload(wait_until=wait_until, timeout=timeout)
            return True
        except Exception as e:
            _log.warning("[page_reload] 刷新失败: %s", e)
            return False
