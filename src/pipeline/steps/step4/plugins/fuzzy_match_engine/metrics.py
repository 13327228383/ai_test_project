# -*- coding: utf-8 -*-
"""KPI 埋点 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。"""
import logging

_log = logging.getLogger(__name__)


def _record_fuzzy_metric(
    candidates_fetched: int,
    concept_score: float,
    char_score: float = 0.0,
    source_intent: str = "",
):
    try:
        from src.backend.state import _thread_run_id as _tid
        from src.core.config_loader import config as _cfg
        from src.extensions.metrics_collector import metrics_collector as _mc

        rid = getattr(_tid, "value", "") or ""
        if rid and _cfg and getattr(_cfg, "project_name", ""):
            _mc.record_fuzzy_match(
                _cfg.project_name,
                rid,
                candidates_fetched,
                float(concept_score),
                float(char_score),
                source_intent[:160],
            )
    except Exception as _e:
        _log.debug("[fuzzy_metrics] 埋点失败(不影响主流程): %s", _e, exc_info=True)
