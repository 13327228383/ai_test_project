# -*- coding: utf-8 -*-
"""
Playwright 浏览器配置 mixin

职责：
- playwright_headless / viewport_width / viewport_height / timeout / screenshot_on_failure
"""


class _ConfigPlaywrightMixin:
    """Playwright 浏览器配置。"""

    @property
    def playwright_headless(self) -> bool:
        return self._get("PLAYWRIGHT_HEADLESS", True)

    @property
    def playwright_viewport_width(self) -> int:
        return self._get("PLAYWRIGHT_VIEWPORT_WIDTH", 1920)

    @property
    def playwright_viewport_height(self) -> int:
        return self._get("PLAYWRIGHT_VIEWPORT_HEIGHT", 1080)

    @property
    def playwright_timeout(self) -> int:
        return self._get("PLAYWRIGHT_TIMEOUT", 30000)

    @property
    def playwright_screenshot_on_failure(self) -> bool:
        return self._get("PLAYWRIGHT_SCREENSHOT_ON_FAILURE", True)
