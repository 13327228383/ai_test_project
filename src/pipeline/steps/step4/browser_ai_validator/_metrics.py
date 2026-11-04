# -*- coding: utf-8 -*-
"""指标记录辅助（唯一实现位于 metrics_collector.record_metric）"""

import logging

from src.extensions.metrics_collector import record_metric as _record_metric

_log = logging.getLogger(__name__)


def _record_guard_rejection(reason_category: str, action_type: str = ""):
    _record_metric(
        "record_guard_rejection",
        {"action_type": action_type, "reason_category": reason_category},
    )


def _record_action_attempt(action_type: str):
    _record_metric("record_action_attempt", {"action_type": action_type})
