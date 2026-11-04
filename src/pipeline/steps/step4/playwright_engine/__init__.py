# -*- coding: utf-8 -*-
"""Playwright 执行引擎子模块包。"""

from .stats import ClickFallbackStats, _click_stats
from .lifecycle import LifecycleMixin
from .dom_extraction import DomExtractionMixin
from .selectors import SelectorMixin
from .actions import ActionsMixin
from .engine import PlaywrightEngine

__all__ = [
    "ClickFallbackStats",
    "_click_stats",
    "PlaywrightEngine",
]
