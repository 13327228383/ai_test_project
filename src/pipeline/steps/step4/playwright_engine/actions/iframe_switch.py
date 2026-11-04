# -*- coding: utf-8 -*-
"""iframe 切换动作 — switch_to_iframe + switch_to_default。

用于嵌套页面、富文本编辑器等 iframe 场景。
维护 _iframe_stack 栈，支持多层嵌套逐层进入和逐层退出。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class IframeSwitchMixin:
    """iframe 切换动作。"""

    async def switch_to_iframe(
        self,
        ai_id: str = "",
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """切换到 iframe 上下文。将当前 page 入栈，后续 self._page 指向 iframe。

        2 级兜底链：ai_id 选择器 → 语义选择器。
        """
        if not self._page:
            return False
        if not ai_id and not target_hint:
            return False

        selector = '[data-ai-id="%s"]' % ai_id if ai_id else None

        # ── 级别0: ai_id 选择器 ──
        if selector:
            with guard("switch_to_iframe: await self._page.wait_for_selector( ", log=_log):
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=3000
                )
                frame_locator = self._page.frame_locator(selector)
                if await frame_locator.count() > 0:
                    if not hasattr(self, "_iframe_stack"):
                        self._iframe_stack = []
                    self._iframe_stack.append(self._page)
                    self._page = frame_locator
                    _log.debug("[switch_to_iframe] 已进入 iframe (ai_id=%s)", ai_id)
                    return True

        # ── 级别1: 语义选择器 ──
        semantic_sel = self._build_semantic_selector(
            target_hint, fallback_selectors or []
        )
        if semantic_sel:
            with guard("switch_to_iframe: frame_locator = self._page.frame_loc", log=_log):
                frame_locator = self._page.frame_locator(semantic_sel)
                if await frame_locator.count() > 0:
                    if not hasattr(self, "_iframe_stack"):
                        self._iframe_stack = []
                    self._iframe_stack.append(self._page)
                    self._page = frame_locator
                    _log.debug(
                        "[switch_to_iframe] 已进入 iframe (语义=%s)", semantic_sel
                    )
                    return True

        return False

    async def switch_to_default(self) -> bool:
        """退出 iframe 上下文，回到父级或顶层 page。"""
        if not hasattr(self, "_iframe_stack") or not self._iframe_stack:
            _log.debug("[switch_to_default] 无 iframe 栈，已在顶层")
            return True

        try:
            self._page = self._iframe_stack.pop()
            _log.debug("[switch_to_default] 已退回到父级上下文")
            return True
        except Exception as e:
            _log.warning("[switch_to_default] 退出 iframe 失败: %s", e)
            return False
