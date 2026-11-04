# -*- coding: utf-8 -*-
"""
辅助函数：占位符填充（指标记录统一走 metrics_collector.record_metric）
"""

import logging

from src.extensions.metrics_collector import record_metric as _record_metric

_log = logging.getLogger(__name__)


def _fill_placeholders(actions: list) -> list:
    from src.core.config_loader import config

    result = []
    for action in actions:
        a = dict(action)
        if a.get("action") == "fill":
            text = a.get("text", "")
            # ★ P2: 占位符走 constants 集中点
            from src.core.constants import (
                LOGIN_PLACEHOLDER_USERNAME,
                LOGIN_PLACEHOLDER_PASSWORD,
            )

            if text == LOGIN_PLACEHOLDER_USERNAME:
                a["text"] = config.target_username or ""
            elif text == LOGIN_PLACEHOLDER_PASSWORD:
                a["text"] = config.target_password or ""
        result.append(a)
    return result
