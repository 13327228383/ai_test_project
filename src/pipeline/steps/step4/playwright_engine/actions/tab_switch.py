# -*- coding: utf-8 -*-
"""标签页切换动作 — switch_tab_element。

用于多标签页场景：按索引或 URL 关键词切换到目标标签页。
页面级操作，不走 _locate_and_act。
"""

import logging

_log = logging.getLogger(__name__)


class TabSwitchMixin:
    """标签页切换动作。"""

    async def switch_tab_element(
        self,
        index: int = -1,
        url_keyword: str = "",
    ) -> bool:
        """切换浏览器标签页。

        优先级：
        1. 若 url_keyword 非空，遍历所有标签页找 URL 包含关键词的
        2. 否则按 index 切换（-1 表示最后一个标签页）
        """
        if not self._page:
            return False

        try:
            context = self._page.context
            pages = context.pages
            if not pages:
                _log.warning("[switch_tab] 无可用标签页")
                return False

            target_page = None

            if url_keyword:
                for p in pages:
                    if url_keyword in p.url:
                        target_page = p
                        break
                if not target_page:
                    _log.debug(
                        "[switch_tab] 未找到 URL 包含 '%s' 的标签页", url_keyword
                    )
                    return False
            elif index >= 0 and index < len(pages):
                target_page = pages[index]
            elif index == -1:
                target_page = pages[-1]
            else:
                _log.warning(
                    "[switch_tab] 索引 %d 超出范围（共 %d 个标签页）", index, len(pages)
                )
                return False

            await target_page.bring_to_front()
            self._page = target_page
            _log.debug("[switch_tab] 已切换到标签页: %s", target_page.url)
            return True
        except Exception as e:
            _log.warning("[switch_tab] 切换标签页失败: %s", e)
            return False
