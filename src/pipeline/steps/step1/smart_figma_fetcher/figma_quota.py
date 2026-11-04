# -*- coding: utf-8 -*-
from __future__ import annotations

"""SmartFigmaFetcher 内部使用的批次配额检查。

依赖 Step1 全局配额追踪器 ``src.pipeline.steps.step1.figma_quota``，
为失败节点批量重试提供“本次批量是否可调用 1 次 API”的预检。
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)


def check_batch_quota(required_calls: int = 1) -> dict[str, Any]:
    """检查剩余配额是否足够发起 ``required_calls`` 次 Figma API 调用。

    返回:
        {
            "ok": bool,               # 是否允许继续
            "is_paid": bool,          # 是否为付费 Token
            "required": int,          # 本次需要 API 调用次数
            "remaining": int | None,  # 免费账号剩余次数；付费为 None
            "message": str,           # ok=False 时的可读原因
        }
    """
    try:
        from src.pipeline.steps.step1.figma_quota import quota_tracker

        status = quota_tracker.get_status()
    except Exception as e:
        _log.error("[figma_quota] 读取配额状态失败: %s", e, exc_info=True)
        return {
            "ok": False,
            "is_paid": False,
            "required": required_calls,
            "remaining": 0,
            "message": "无法读取 Figma 配额状态: %s" % str(e)[:120],
        }

    is_paid = status.get("plan_type") == "paid"
    if is_paid:
        return {
            "ok": True,
            "is_paid": True,
            "required": required_calls,
            "remaining": None,
            "message": "",
        }

    remaining = status.get("remaining", 0) or 0
    if remaining < required_calls:
        return {
            "ok": False,
            "is_paid": False,
            "required": required_calls,
            "remaining": int(remaining),
            "message": (
                "Figma 免费 API 配额不足: 本次批量需要 %d 次调用，"
                "剩余 %d 次（本月已用 %d/%d）"
                % (
                    required_calls,
                    remaining,
                    status.get("total_used", 0),
                    status.get("monthly_quota", 6),
                )
            ),
        }

    return {
        "ok": True,
        "is_paid": False,
        "required": required_calls,
        "remaining": int(remaining),
        "message": "",
    }
