# -*- coding: utf-8 -*-
"""
semantic_dom_tree.utils._metrics — KPI 埋点
"""
import logging

_log = logging.getLogger(__name__)


def _record_tree_metric(
    total_regions: int, total_elements: int, dominant_region: str = ""
):
    try:
        from src.backend.state import _thread_run_id as _tid
        from src.core.config_loader import config as _cfg
        from src.extensions.metrics_collector import metrics_collector as _mc

        rid = getattr(_tid, "value", "") or ""
        if rid and _cfg and getattr(_cfg, "project_name", ""):
            _mc.record_region_tree(
                _cfg.project_name,
                rid,
                int(total_regions),
                int(total_elements),
                (dominant_region or "")[:80],
            )
    except Exception as _e:
        _log.debug("[tree_metrics] 埋点失败(不影响主流程): %s", _e, exc_info=True)
