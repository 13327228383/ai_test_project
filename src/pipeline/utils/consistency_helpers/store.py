# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Store 加载辅助
"""

from typing import Any

from .base import _make_issue
import logging

_log = logging.getLogger(__name__)


def _try_health_stats(store_obj: Any | None) -> dict[str, Any]:
    """安全调用 store.health_stats()，失败返回空 dict。"""
    if store_obj is None:
        return {}
    try:
        return store_obj.health_stats() or {}
    except Exception as _e:
        _log.debug("[store] _try_health_stats 异常: %s", _e, exc_info=True)
        return {}


def _try_load_store_data(
    store_obj: Any | None,
    load_method: str,
    error_code: str,
    error_severity: str = "error",
    error_message: str = "",
    empty_default: Any = None,
) -> tuple:
    """安全调用 store 的加载方法，返回 (data, issues)。"""
    if store_obj is None:
        return empty_default, []
    try:
        data = getattr(store_obj, load_method)()
        return data, []
    except Exception as e:
        return empty_default, [
            _make_issue(
                error_code,
                error_severity,
                error_message or "%s 读取失败" % load_method,
                [str(e)[:200]],
            )
        ]
