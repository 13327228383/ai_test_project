# -*- coding: utf-8 -*-
"""聚焦/失焦动作 — focus_element + blur_element。

用于表单校验触发（onFocus/onBlur 事件）、焦点样式验证等场景。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging

_log = logging.getLogger(__name__)


class FocusMixin:
    """聚焦与失焦动作。"""

    async def focus_element(
        self,
        ai_id: str,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """将焦点移至指定元素。触发 onFocus 事件。2 级兜底链。"""

        async def _do(el) -> bool:
            try:
                await el.focus(timeout=5000)
                return True
            except Exception as e:
                _log.debug("[focus] 原生 focus 失败，尝试 JS 备选: %s", e)
                try:
                    await el.evaluate("el => el.focus()")
                    return True
                except Exception as e2:
                    _log.debug("[focus] JS 备选也失败: %s", e2)
                    return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "focus"
        )

    async def blur_element(
        self,
        ai_id: str,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """从指定元素移除焦点。触发 onBlur 事件。2 级兜底链。"""

        async def _do(el) -> bool:
            try:
                await el.blur(timeout=5000)
                return True
            except Exception as e:
                _log.debug("[blur] 原生 blur 失败，尝试 JS 备选: %s", e)
                try:
                    await el.evaluate("el => el.blur()")
                    return True
                except Exception as e2:
                    _log.debug("[blur] JS 备选也失败: %s", e2)
                    return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "blur"
        )
