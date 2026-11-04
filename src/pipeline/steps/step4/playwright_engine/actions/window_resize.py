# -*- coding: utf-8 -*-
"""窗口调整动作 — resize_window_element。

用于响应式布局测试：调整浏览器视口宽高，验证页面在不同尺寸下的布局表现。
页面级操作，不走 _locate_and_act。
"""

import logging

_log = logging.getLogger(__name__)


class WindowResizeMixin:
    """窗口调整动作。"""

    async def resize_window_element(
        self,
        width: int = 1920,
        height: int = 1080,
    ) -> bool:
        """调整浏览器视口尺寸为 width x height。"""
        if not self._page:
            return False
        if width <= 0 or height <= 0:
            _log.warning("[resize] 无效尺寸: %dx%d", width, height)
            return False
        try:
            await self._page.set_viewport_size({"width": width, "height": height})
            _log.debug("[resize] 视口已调整为 %dx%d", width, height)
            return True
        except Exception as e:
            _log.warning("[resize] 调整视口失败: %s", e)
            return False
