# -*- coding: utf-8 -*-
"""
覆盖机制
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)

_OVERRIDE_LIMIT = None


def set_override(limit: int) -> bool:
    """设置一个用户覆盖的上限（永久生效直到 clear_override）。"""
    global _OVERRIDE_LIMIT
    try:
        if not isinstance(limit, int) or limit <= 0:
            _log.warning(
                "dynamic_dom_sizer.set_override: limit 必须是正整数，收到 %s", limit
            )
            return False
        _OVERRIDE_LIMIT = limit
        _log.info("DynamicDomSizer: 已设置覆盖 limit=%d", limit)
        return True
    except Exception as e:
        _log.warning("dynamic_dom_sizer.set_override 异常 %s", e)
        return False


def clear_override() -> None:
    """清除自定义覆盖。"""
    global _OVERRIDE_LIMIT
    with guard("clear_override: _OVERRIDE_LIMIT = None", log=_log, level=logging.WARNING):
        _OVERRIDE_LIMIT = None
        _log.info("DynamicDomSizer: 覆盖已清除")
