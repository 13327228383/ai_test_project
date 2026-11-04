# -*- coding: utf-8 -*-
from __future__ import annotations
from playwright.async_api import Browser, BrowserContext

"""Playwright 引擎主类：组合所有 Mixin。"""

from playwright.async_api import Page

from .lifecycle import LifecycleMixin
from .dom_extraction import DomExtractionMixin
from .selectors import SelectorMixin
from .actions import ActionsMixin


class PlaywrightEngine(LifecycleMixin, DomExtractionMixin, SelectorMixin, ActionsMixin):
    """Playwright 纯引擎：浏览器生命周期、DOM 剪枝、data-ai-id 操作、截图。"""

    def __init__(
        self,
        headless: bool = False,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        browser_info: dict = None,
        timeout_ms: int = 30000,
        launch_timeout_s: float = 30.0,
    ) -> None:
        # 视口默认值取配置（`config.playwright_viewport_*`）——「默认视口」只能有一处定义，
        # 否则断言里的"默认 viewport"与实际渲染视口不一致（headless 下窗口默认 800×600，
        # 与配置 1920 脱钩 → 宽表格在窄视口下越界，被误判为破版缺陷）。
        from src.core.config_loader import config as _cfg

        self._headless = headless
        self._viewport_width = int(viewport_width or _cfg.playwright_viewport_width)
        self._viewport_height = int(viewport_height or _cfg.playwright_viewport_height)
        self._browser_info = browser_info or {}
        self._timeout_ms = timeout_ms
        # ★ [已复核-DeepSeek-V3 2026-08-16] 浏览器启动硬超时（秒），供 LifecycleMixin 使用。
        self._launch_timeout = launch_timeout_s
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_original_count: int = 0
