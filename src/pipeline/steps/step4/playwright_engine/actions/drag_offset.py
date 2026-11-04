# -*- coding: utf-8 -*-
"""偏移拖拽动作 — drag_offset_element。

用于滑块验证码、画布拖拽、自定义偏移量拖拽等场景。
从元素中心按 (dx, dy) 偏移执行拖拽，多级鼠标事件模拟。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging

_log = logging.getLogger(__name__)


class DragOffsetMixin:
    """偏移拖拽动作。"""

    async def drag_offset_element(
        self,
        ai_id: str,
        dx: int = 0,
        dy: int = 0,
        steps: int = 10,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """从元素中心按 (dx, dy) 偏移执行拖拽。

        steps 控制鼠标移动的中间帧数（越大越平滑）。
        2 级兜底链：ai_id 选择器 → 语义选择器。
        """

        async def _do(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                box = await el.bounding_box()
                if not box:
                    _log.debug("[drag_offset] 无法获取元素 bounding_box")
                    return False
                sx = box["x"] + box["width"] / 2
                sy = box["y"] + box["height"] / 2
                ex = sx + dx
                ey = sy + dy
                await self._page.mouse.move(sx, sy)
                await self._page.mouse.down()
                try:
                    await self._page.mouse.move(ex, ey, steps=steps)
                finally:
                    await self._page.mouse.up()  # ★ BE-007: 确保 mouse up 无论异常
                return True
            except Exception as e:
                _log.debug("[drag_offset] 坐标拖拽失败: %s", e)
                return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "drag_offset"
        )
