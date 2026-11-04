# -*- coding: utf-8 -*-
"""
KPI 埋点
"""

import logging

_log = logging.getLogger(__name__)


def _record_sizer_metric(limit: int, keyword: str = "", mode: str = ""):
    """记录 sizer 决策指标。"""
    try:
        from src.backend.state import _thread_run_id as _tid
        from src.core.config_loader import config as _cfg
        from src.extensions.metrics_collector import metrics_collector as _mc

        rid = getattr(_tid, "value", "") or ""
        if rid and _cfg and getattr(_cfg, "project_name", ""):
            _mc.record_dom_sizer(
                _cfg.project_name,
                rid,
                limit,
                intent_keyword=keyword[:60],
                context_mode=mode,
            )
    except Exception as _e:
        _log.debug("[_metric] _record_sizer_metric 异常: %s", _e, exc_info=True)
