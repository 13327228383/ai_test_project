# -*- coding: utf-8 -*-
"""
smart_figma_fetcher 的配额检测子模块
====================================
零业务逻辑变更，仅做物理代码搬迁。
"""

from src.pipeline.steps.step1._smart_fetcher_utils import detect_paid_token
from src.core.log_utils import fmt_log
import logging

_log = logging.getLogger(__name__)

# 免费账号配额估算：Tier 1 端点约 6 次/月
# 付费账号（figd_ Token）：500 次/分钟，无月度限制
_FREE_TIER1_MONTHLY_QUOTA = 6
_PAID_RATE_LIMIT_PER_MINUTE = 500


def check_quota(api_call_count: int, caller: str) -> bool:
    """检查是否接近或超过配额。付费账号仅限制每分钟频率，无月度限制。"""
    is_paid = detect_paid_token()
    if is_paid:
        if api_call_count >= _PAID_RATE_LIMIT_PER_MINUTE:
            _log.info(
                fmt_log(
                    "Figma API",
                    "%s 已超过付费频率限制 (%d/min)，请稍后重试"
                    % (caller, _PAID_RATE_LIMIT_PER_MINUTE),
                )
            )
            return False
        return True

    if api_call_count >= _FREE_TIER1_MONTHLY_QUOTA:
        _log.info(
            fmt_log(
                "Figma API",
                "%s 已超过免费配额 (%d/%d)，禁止新 API 调用"
                % (caller, api_call_count, _FREE_TIER1_MONTHLY_QUOTA),
            )
        )
        return False
    if api_call_count >= _FREE_TIER1_MONTHLY_QUOTA - 1:
        _log.warning(
            fmt_log(
                "Figma API",
                "⚠ %s 接近免费配额上限 (%d/%d)，这是最后一次调用"
                % (caller, api_call_count + 1, _FREE_TIER1_MONTHLY_QUOTA),
            )
        )
    return True


def get_quota_info(api_call_count: int) -> dict:
    """返回当前配额信息，供预检 API 使用。"""
    is_paid = detect_paid_token()
    if is_paid:
        return {
            "plan_type": "paid",
            "used_this_session": api_call_count,
            "rate_limit_per_minute": _PAID_RATE_LIMIT_PER_MINUTE,
            "remaining_per_minute": max(
                0, _PAID_RATE_LIMIT_PER_MINUTE - api_call_count
            ),
            "monthly_quota": None,
        }
    return {
        "plan_type": "free",
        "used_this_session": api_call_count,
        "monthly_quota": _FREE_TIER1_MONTHLY_QUOTA,
        "remaining": max(0, _FREE_TIER1_MONTHLY_QUOTA - api_call_count),
    }
