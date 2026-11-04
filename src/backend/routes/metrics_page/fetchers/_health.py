# -*- coding: utf-8 -*-
"""
健康度汇总获取器
"""

import logging

from ._utils import _current_project_id

_log = logging.getLogger(__name__)


def _fetch_health_summary() -> dict:
    """从 health.py 接口获取健康度汇总数据（含 confidence / overall_score）。"""
    from src.backend.routes.health import api_health_summary

    try:
        project = _current_project_id()
        if not project:
            return {"note": "需先选择项目"}

        resp = api_health_summary()
        if hasattr(resp, "get_json"):
            data = resp.get_json(silent=True)
        elif isinstance(resp, (tuple, list)):
            data = resp[0] if hasattr(resp[0], "get_json") else None
            if data and hasattr(data, "get_json"):
                data = data.get_json(silent=True)
        else:
            data = resp

        if not data or not data.get("ok"):
            return {"has_data": False, "note": "健康度数据不可用"}

        confidence = data.get("confidence")
        if isinstance(confidence, dict):
            levels = {"high": 0, "medium": 0, "low": 0}
            for item in confidence.values():
                if isinstance(item, dict):
                    lv = item.get("level", "medium")
                    if lv in levels:
                        levels[lv] += 1
            total = sum(levels.values())
            if total > 0:
                high_ratio = round(levels["high"] / total * 100, 1)
                confidence_summary = f"高置信占比 {high_ratio}%"
            else:
                confidence_summary = None
        else:
            confidence_summary = confidence

        return {
            "has_data": data.get("has_data", False),
            "overall_score": data.get("overall_score"),
            "confidence": confidence_summary,
            "run_id": data.get("run_id"),
            "run_count": data.get("run_count"),
        }
    except Exception as e:
        return {"note": f"读取异常: {e}"}
