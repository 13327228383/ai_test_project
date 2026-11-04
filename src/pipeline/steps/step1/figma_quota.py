# -*- coding: utf-8 -*-
"""
Figma API 配额持久化追踪 — v4.0
=========================
追踪 Figma 免费 API 的每月调用配额（6次/月），检测 429 限流状态。

**事实源迁移：** 从 ``figma_api_quota.json 已从 JSON 迁移至 MySQL
``figma_api_quota`` 表（参见 `src/pipeline/utils/figma_state_store.py`_。
本文件作为 Step1 API 调用链路的唯一事实源来自 MySQL；
旧 ``projects/<PROJECT>/figma_api_quota.json`` 仅保留为显式导入源 / 导出物，
不再参与 Step1 主运行链路兜底。

使用方式：
    from src.pipeline.steps.step1.figma_quota import quota_tracker
    quota_tracker.record_call(success=True)
    status = quota_tracker.get_status()
"""

import time
from typing import Any
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _format_seconds(secs: int) -> str:
    """格式化秒数为可读字符串：'12h 30m' / '45m' / '30s'。"""
    if secs <= 0:
        return "0s"
    h, m = divmod(secs, 3600)
    m, s = divmod(m, 60)
    parts = []
    if h:
        parts.append("%dh" % h)
    if m:
        parts.append("%dm" % m)
    if not parts and s:
        parts.append("%ds" % s)
    return " ".join(parts)


def _default_quota_store():
    """懒加载 figma_state_store，避免模块导入时触发 DB 连接失败。"""
    from src.pipeline.utils.figma_state_store import figma_state_store

    return figma_state_store


# ---------------------------------------------------------------------------
# FigmaQuotaTracker — API 配额持久化追踪
# ---------------------------------------------------------------------------
class FigmaQuotaTracker:
    """
    持久化追踪 Figma API 调用次数和限流状态。

    事实源：MySQL ``figma_api_quota`` 表（src/pipeline/utils/figma_state_store.py_
    默认实现细节请参考 `figma_state_store`。本类负责内存缓存。
    """

    _FREE_MONTHLY_QUOTA = 6

    def __init__(self):
        self._data: dict[str, Any] = self._load()

    @staticmethod
    def _detect_paid_token() -> bool:
        """检测当前 Figma Token 是否为付费账号（figd_ 开头 = PAT Token）。
        Token 存储在用户凭证 DB 的 FIGMA_KEY 字段。"""
        try:
            from src.core.config_loader import config

            token = config.figma_key
            return bool(token and isinstance(token, str) and token.startswith("figd_"))
        except Exception as _e:
            _log.debug("[figma_quota] _detect_paid_token 异常: %s", _e, exc_info=True)
            return False

    @property
    def is_paid_plan(self) -> bool:
        return self._detect_paid_token()

    @property
    def effective_quota(self) -> int:
        """返回有效月度配额：付费 None（无限制），免费 6。"""
        return None if self._detect_paid_token() else self._FREE_MONTHLY_QUOTA

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def record_call(self, success: bool, error_msg: str = None):
        """记录一次 API 调用。自动处理月初重置。429 时立刻将配额置满（仅免费账号）。"""
        self._ensure_month()
        now = time.time()
        self._data["total_used"] = self._data.get("total_used", 0) + 1
        self._data["last_call_at"] = now

        if success:
            self._data["last_error"] = None
        else:
            self._data["last_error"] = error_msg
            if error_msg and ("429" in error_msg or "rate limit" in error_msg.lower()):
                if not self._detect_paid_token():
                    self._data["total_used"] = self._FREE_MONTHLY_QUOTA
                retry_secs = self._parse_retry_after(error_msg)
                if retry_secs:
                    self._data["rate_limit_until"] = now + retry_secs

        self._save()

    def get_status(self) -> dict[str, Any]:
        """返回当前配额状态，供 GUI 显示。付费账号不限制月度次数。"""
        self._ensure_month()
        now = time.time()
        rate_limit_until = self._data.get("rate_limit_until", 0) or 0
        is_rate_limited = now < rate_limit_until
        total_used = self._data.get("total_used", 0)
        is_paid = self._detect_paid_token()

        remaining = None if is_paid else max(0, self._FREE_MONTHLY_QUOTA - total_used)
        monthly_quota = None if is_paid else self._FREE_MONTHLY_QUOTA
        retry_left = int(rate_limit_until - now) if is_rate_limited else 0

        return {
            "plan_type": "paid" if is_paid else "free",
            "total_used": total_used,
            "monthly_quota": monthly_quota,
            "remaining": remaining,
            "is_rate_limited": is_rate_limited,
            "rate_limit_until": rate_limit_until,
            "rate_limit_until_str": (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(rate_limit_until))
                if rate_limit_until > 0
                else ""
            ),
            "retry_left_seconds": retry_left,
            "retry_left_str": _format_seconds(retry_left) if retry_left > 0 else "",
            "last_call_at": self._data.get("last_call_at", 0) or 0,
            "last_error": self._data.get("last_error", ""),
            "current_month": self._data.get("current_month", ""),
        }

    # ------------------------------------------------------------------
    # 内部：MySQL 持久化
    # ------------------------------------------------------------------
    def should_warn_before_fetch(self) -> dict[str, Any]:
        self._ensure_month()
        now = time.time()
        used = self._data.get("total_used", 0)
        last = self._data.get("last_call_at", 0) or 0
        days_since = (now - last) / 86400 if last > 0 else 999
        is_paid = self._detect_paid_token()

        warn = False
        msg = ""
        if used >= 2 and days_since < 5 and not is_paid:
            warn = True
            msg = (
                "⚠ Figma API 每月仅 6 次免费配额，本月已用 %d 次。\n距离上次拉取仅 %.1f 天。\n如无必要，建议设计定稿后再拉取最新版本。"
            ) % (used, days_since)
        return {
            "warn": warn,
            "message": msg,
            "used": used,
            "quota": None if is_paid else self._FREE_MONTHLY_QUOTA,
            "days_since_last": round(days_since, 1),
            "plan_type": "paid" if is_paid else "free",
        }

    def _ensure_month(self):
        current_month = time.strftime("%Y-%m")
        if self._data.get("current_month") != current_month:
            self._data = {
                "current_month": current_month,
                "total_used": 0,
                "rate_limit_until": 0,
                "last_call_at": 0,
                "last_error": None,
            }
            self._save()

    def _load(self) -> dict:
        try:
            data = _default_quota_store().load_quota()
            if isinstance(data, dict) and data.get("current_month"):
                return data
        except Exception as _e:
            _log.error("[figma_quota] _load 异常: %s", _e, exc_info=True)
        return {
            "current_month": time.strftime("%Y-%m"),
            "total_used": 0,
            "rate_limit_until": 0,
            "last_call_at": 0,
            "last_error": None,
        }

    def _save(self):
        try:
            _default_quota_store().save_quota(self._data)
        except Exception as _e:
            _log.error("[figma_quota] _save 异常: %s", _e, exc_info=True)

    @staticmethod
    def _parse_retry_after(error_msg: str) -> int:
        """从 429 错误消息中提取 Retry-After 秒数。"""
        import re

        m = re.search(r"Retry after (\d+) seconds", error_msg)
        if m:
            return int(m.group(1))
        m = re.search(r"retry[_-]?after[=:]\s*(\d+)", error_msg, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0


# ---------------------------------------------------------------------------
# 模块级单例（与原模块保持一致）
# ---------------------------------------------------------------------------
# ★ 方案C 改造：quota_tracker 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

quota_tracker = PerProjectRegistry(FigmaQuotaTracker)
