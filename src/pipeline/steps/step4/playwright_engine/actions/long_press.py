# -*- coding: utf-8 -*-
"""长按动作 — long_press_element。

用于移动端右键菜单触发、拖拽前奏、自定义长按交互等场景。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import asyncio
import logging

_log = logging.getLogger(__name__)


class LongPressMixin:
    """长按动作。"""

    async def long_press_element(
        self,
        ai_id: str,
        duration_ms: int = 1000,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """长按指定元素。鼠标按下后保持 duration_ms 毫秒再释放。2 级兜底链。"""

        async def _do(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                box = await el.bounding_box()
                if not box:
                    _log.debug("[long_press] 无法获取元素 bounding_box")
                    return False
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await self._page.mouse.move(cx, cy)
                await self._page.mouse.down()
                try:
                    await asyncio.sleep(duration_ms / 1000.0)
                finally:
                    await self._page.mouse.up()  # ★ BE-006: 确保 mouse up 无论异常
                return True
            except Exception as e:
                _log.debug("[long_press] 坐标模拟失败: %s", e)
                return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "long_press"
        )
