# -*- coding: utf-8 -*-
"""裸按键动作 — key_down_element + key_up_element。

与 press_key 的区别：press_key 是 down+up 一体；
key_down/key_up 分离，支持"按住 Shift 多选""按住 Ctrl 点选"等组合操作。
"""

import logging

_log = logging.getLogger(__name__)


class KeyPressMixin:
    """裸按键按下/释放动作。"""

    async def key_down_element(self, key: str) -> bool:
        """按下键盘按键（不释放）。用于组合键操作。

        key 格式如 'Shift' / 'Control' / 'Alt' / 'Meta'。
        """
        if not self._page or not key:
            return False
        try:
            await self._page.keyboard.down(key)
            return True
        except Exception as e:
            _log.warning("[key_down] 按键 %s 按下失败: %s", key, e)
            return False

    async def key_up_element(self, key: str) -> bool:
        """释放键盘按键。与 key_down 配合使用。"""
        if not self._page or not key:
            return False
        try:
            await self._page.keyboard.up(key)
            return True
        except Exception as e:
            _log.warning("[key_up] 按键 %s 释放失败: %s", key, e)
            return False
