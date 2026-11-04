# -*- coding: utf-8 -*-
"""页面级动作（导航/滚动/拖拽/上传）— 从 actions.py 拆出，零逻辑变更。"""

import logging
from src.core.guarded import guard
from pathlib import Path

_log = logging.getLogger(__name__)


class MotionMixin:
    """scroll_page / navigate_to / drag_element / upload_file_element。"""

    async def scroll_page(self, direction: str = "down", amount: int = 500) -> bool:
        """页面滚动操作，供 AI 探索隐藏元素。"""
        if not self._page:
            return False
        try:
            scroll_js = {
                "bottom": "window.scrollTo(0, document.body.scrollHeight)",
                "top": "window.scrollTo(0, 0)",
                "down": "window.scrollBy(0, %d)" % amount,
                "up": "window.scrollBy(0, -%d)" % amount,
            }.get(direction.lower(), "window.scrollBy(0, %d)" % amount)
            await self._page.evaluate(scroll_js)
            return True
        except Exception as e:
            _log.warning("[scroll_page] 滚动失败: %s", e)
            return False
