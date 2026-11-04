# -*- coding: utf-8 -*-
"""滑动手势动作 — swipe_element。

用于移动端场景：从元素中心按指定方向和距离执行滑动手势。
支持方向：up/down/left/right，或自定义 dx/dy。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging

_log = logging.getLogger(__name__)


class SwipeMixin:
    """滑动手势动作。"""

    async def swipe_element(
        self,
        ai_id: str,
        direction: str = "up",
        distance: int = 200,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """从元素中心执行滑动手势。

        direction: up/down/left/right，决定 dx/dy 方向。
        distance: 滑动距离（像素）。
        2 级兜底链：ai_id 选择器 → 语义选择器。
        """

        dir_map = {
            "up": (0, -distance),
            "down": (0, distance),
            "left": (-distance, 0),
            "right": (distance, 0),
        }
        dx, dy = dir_map.get(direction, (0, -distance))

        async def _do(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                box = await el.bounding_box()
                if not box:
                    _log.debug("[swipe] 无法获取元素 bounding_box")
                    return False
                sx = box["x"] + box["width"] / 2
                sy = box["y"] + box["height"] / 2
                ex = sx + dx
                ey = sy + dy
                await self._page.mouse.move(sx, sy)
                await self._page.mouse.down()
                await self._page.mouse.move(ex, ey, steps=15)
                await self._page.mouse.up()
                return True
            except Exception as e:
                _log.debug("[swipe] 滑动失败: %s", e)
                return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "swipe"
        )
