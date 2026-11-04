# -*- coding: utf-8 -*-
"""原生滚轮动作 — wheel_element。

与 scroll_page 的区别：scroll_page 用 window.scrollBy JS 执行；
wheel 模拟原生鼠标滚轮事件，能触发 Canvas/地图/缩放容器等
只响应原生 wheel 事件 的场景。
"""

import logging

_log = logging.getLogger(__name__)


class WheelMixin:
    """原生滚轮动作。"""

    async def wheel_element(
        self,
        delta_x: int = 0,
        delta_y: int = 300,
    ) -> bool:
        """模拟鼠标滚轮滚动。

        delta_x: 水平滚动量（正值向右）
        delta_y: 垂直滚动量（正值向下）
        """
        if not self._page:
            return False
        try:
            await self._page.mouse.wheel(delta_x, delta_y)
            return True
        except Exception as e:
            _log.warning("[wheel] 滚轮失败: %s", e)
            return False
