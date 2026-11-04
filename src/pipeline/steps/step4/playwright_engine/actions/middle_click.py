# -*- coding: utf-8 -*-
"""中键点击动作 — middle_click_element。

用于"在新标签页打开链接"等中键交互场景。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging

_log = logging.getLogger(__name__)


class MiddleClickMixin:
    """中键点击动作。"""

    async def middle_click_element(
        self,
        ai_id: str,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """中键点击指定元素。2 级兜底链。"""

        async def _do(el) -> bool:
            try:
                await el.click(button="middle", timeout=5000)
                return True
            except Exception as e:
                _log.debug("[middle_click] 原生 middle click 失败: %s", e)
                return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "middle_click"
        )
