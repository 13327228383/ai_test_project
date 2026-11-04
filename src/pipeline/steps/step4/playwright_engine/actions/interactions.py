# -*- coding: utf-8 -*-
"""hover/select_option/press/double_click/right_click/check 等轻量交互动作 — 从 actions.py 拆出，零逻辑变更。"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class InteractionsMixin:
    """通用 _locate_and_act + 6 个交互动作。"""

    async def _locate_and_act(
        self,
        ai_id: str,
        target_hint: str,
        fallback_selectors: list,
        action_fn,
        tag: str = "action",
    ) -> bool:
        """通用 2 级兜底定位+执行：ai_id 选择器 → 语义选择器。action_fn(el) -> bool。"""
        if not self._page:
            return False
        if not ai_id and not target_hint:
            return False

        selector = '[data-ai-id="%s"]' % ai_id if ai_id else None

        # ── 级别0: ai_id 选择器 ──
        if selector:
            with guard("_locate_and_act: await self._page.wait_for_selector( ", log=_log):
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=3000
                )
                element = self._page.locator(selector).first
                await element.scroll_into_view_if_needed()
                if await action_fn(element):
                    return True

        # ── 级别1: 语义选择器重定位 ──
        semantic_sel = self._build_semantic_selector(
            target_hint, fallback_selectors or []
        )
        if semantic_sel:
            with guard("_locate_and_act: el = self._page.locator(semantic_sel", log=_log):
                el = self._page.locator(semantic_sel).first
                if await el.count() > 0:
                    await el.scroll_into_view_if_needed()
                    if await action_fn(el):
                        return True

        return False
