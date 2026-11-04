# -*- coding: utf-8 -*-
"""右键菜单交互动作 — context_menu_click_element。

用于右键菜单场景：先右键点击目标元素弹出上下文菜单，再点击菜单中的指定项。
2 级兜底链：ai_id 选择器 → 语义选择器。
"""

import asyncio
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)


class ContextMenuMixin:
    """右键菜单交互动作。"""

    async def context_menu_click_element(
        self,
        ai_id: str,
        menu_item_hint: str = "",
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """右键点击目标元素弹出上下文菜单，再点击菜单中的 menu_item_hint 项。

        步骤：
        1. 右键点击目标元素
        2. 等待菜单出现（短暂等待）
        3. 按文本定位并点击菜单项

        2 级兜底链（针对目标元素定位）。
        """
        if not self._page:
            return False
        if not ai_id and not target_hint:
            return False
        if not menu_item_hint:
            _log.warning("[context_menu] 缺少 menu_item_hint 参数")
            return False

        selector = '[data-ai-id="%s"]' % ai_id if ai_id else None

        async def _do_right_click(el) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                await el.click(button="right", timeout=5000)
                # ★ S4-014: 等待菜单出现（确定性等待替代固定 sleep）
                try:
                    menu_el = self._page.get_by_text(menu_item_hint, exact=False).first
                    await menu_el.wait_for(state="visible", timeout=5000)
                except Exception:
                    # 超时也没关系，继续尝试点击
                    await asyncio.sleep(0.3)
                await menu_el.click(timeout=5000)
                return True
            except Exception as e:
                _log.debug("[context_menu] 菜单交互失败: %s", e)
                return False

        # ── 级别0: ai_id 选择器 ──
        if selector:
            with guard("context_menu_click_element: await self._page.wait_for_selector( ", log=_log):
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=3000
                )
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    if await _do_right_click(el):
                        return True

        # ── 级别1: 语义选择器 ──
        semantic_sel = self._build_semantic_selector(
            target_hint, fallback_selectors or []
        )
        if semantic_sel:
            with guard("context_menu_click_element: el = self._page.locator(semantic_sel", log=_log):
                el = self._page.locator(semantic_sel).first
                if await el.count() > 0:
                    if await _do_right_click(el):
                        return True

        return False
