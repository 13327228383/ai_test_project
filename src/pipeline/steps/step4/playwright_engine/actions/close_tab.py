# -*- coding: utf-8 -*-
"""关闭标签页动作 — close_tab_element。

关闭当前浏览器标签页。如果有多个标签页，关闭后自动切换到下一个。
用于"关闭弹窗页/关闭详情页后回到列表"等场景。
"""

import logging

_log = logging.getLogger(__name__)


class CloseTabMixin:
    """关闭标签页动作。"""

    async def close_tab_element(self) -> bool:
        """关闭当前标签页。

        如果当前是唯一标签页，关闭后浏览器会创建新空白页。
        如果有多个标签页，关闭后自动切换到剩余的第一个。
        """
        if not self._page:
            return False
        try:
            context = self._page.context
            pages = context.pages
            if len(pages) <= 1:
                _log.info("[close_tab] 仅有一个标签页，关闭后将创建新空白页")
            await self._page.close()
            # 如果还有其他页面，切换到第一个；否则新建空白页
            remaining = context.pages
            if remaining:
                self._page = remaining[0]
            else:
                # ★ P0-fix(2026-07-24): 不再置 None（会导致后续 get_structured_state 崩溃）
                #   原bug: 关闭最后一个标签页后 _page=None → _ai_loop 重试 → 崩溃
                #   修复: 新建空白 page，保持 _page 永不失效（about:blank 是安全降级）
                #   通用性: 适用于任何"关闭最后标签页"场景（测试结束/弹窗关闭/异常恢复）
                #   合理性: 追根溯源到 _page 生命周期管理，而非在调用方打补丁
                #   覆盖性: 覆盖所有 close_tab 调用路径
                self._page = await context.new_page()
                _log.info(
                    "[close_tab] 已新建空白页（about:blank）作为降级，_page 永不失效"
                )
            return True
        except Exception as e:
            _log.warning("[close_tab] 关闭标签页失败: %s", e)
            return False
