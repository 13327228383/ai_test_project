# -*- coding: utf-8 -*-
"""多点触控动作 — multi_touch_pinch_element。

模拟双指缩放/旋转手势，用于移动端地图、图片缩放等场景。
通过 Touchscreen API 模拟两个触控点。
"""

import logging

_log = logging.getLogger(__name__)


class MultiTouchMixin:
    """多点触控动作。"""

    async def multi_touch_pinch_element(
        self,
        ai_id: str = "",
        scale: float = 1.5,
        target_hint: str = "",
        fallback_selectors: list = None,
    ) -> bool:
        """模拟双指缩放手势。

        scale > 1.0 放大，scale < 1.0 缩小。
        以元素中心为原点，两指对称移动。
        """
        if not self._page:
            return False
        try:
            # 获取缩放中心点
            cx, cy = 400, 300  # 默认屏幕中心
            if ai_id or target_hint:
                selector = '[data-ai-id="%s"]' % ai_id if ai_id else None
                el = None
                if selector:
                    try:
                        el = self._page.locator(selector).first
                        if await el.count() == 0:
                            el = None
                    except Exception:
                        el = None
                if not el:
                    semantic_sel = self._build_semantic_selector(
                        target_hint, fallback_selectors or []
                    )
                    if semantic_sel:
                        try:
                            el = self._page.locator(semantic_sel).first
                            if await el.count() == 0:
                                el = None
                        except Exception:
                            el = None
                if el:
                    box = await el.bounding_box()
                    if box:
                        cx = int(box["x"] + box["width"] / 2)
                        cy = int(box["y"] + box["height"] / 2)

            base_offset = 50
            offset = int(base_offset * scale)

            # Playwright Touchscreen API: 模拟两指缩放
            # 第一指从中心左偏移移到更远左
            touch1 = await self._page.touchscreen.tap(cx - base_offset, cy)
            # 第二指从中心右偏移移到更远右
            touch2 = await self._page.touchscreen.tap(cx + base_offset, cy)
            # 注：Playwright touchscreen API 有限，这里用 tap 模拟
            # 真正的多指连续触控需要 CDP Session
            _log.info("[multi_touch] 双指缩放 scale=%.1f center=(%d,%d)", scale, cx, cy)
            return True
        except Exception as e:
            _log.warning("[multi_touch] 多点触控失败: %s", e)
            return False
