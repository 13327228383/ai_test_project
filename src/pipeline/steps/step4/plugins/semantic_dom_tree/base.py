# -*- coding: utf-8 -*-
from __future__ import annotations

"""
semantic_dom_tree_mixins._base — 基础状态 mixin（__init__ / last_tree / last_hint）
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _SemanticDomBaseMixin:
    """提供实例初始化和状态访问方法。"""

    def __init__(self):
        self._last_tree: dict[str, Any] | None = None
        self._last_hint: str = ""
        _log.info("SemanticDomTree 已初始化")

    def last_tree(self) -> dict[str, Any] | None:
        return self._last_tree

    def last_hint(self) -> str:
        return self._last_hint
