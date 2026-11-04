# -*- coding: utf-8 -*-
"""清空输入动作 — clear_element。

与 fill("") 的区别：clear 调用 Playwright 原生 clear() 方法，
正确触发 focus + selectAll + delete 事件链，确保 onChange 触发。
用于表单重置、清空校验等场景。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import logging

_log = logging.getLogger(__name__)


class ClearMixin:
    """清空输入框动作。"""

    async def clear_element(
        self,
        ai_id: str,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """清空输入框内容。触发完整的清除事件链。2 级兜底链。"""

        async def _do(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                await el.clear(timeout=5000)
                return True
            except Exception as e:
                _log.debug("[clear] 原生 clear 失败，尝试 JS 备选: %s", e)
                try:
                    await el.evaluate(
                        "(el) => { el.focus(); el.select(); "
                        "document.execCommand('delete'); "
                        "el.dispatchEvent(new Event('input', {bubbles:true})); "
                        "el.dispatchEvent(new Event('change', {bubbles:true})); }"
                    )
                    return True
                except Exception as e2:
                    _log.debug("[clear] JS 备选也失败: %s", e2)
                    return False

        return await self._locate_and_act(
            ai_id, target_hint, fallback_selectors, _do, "clear"
        )
