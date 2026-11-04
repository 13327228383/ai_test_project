# -*- coding: utf-8 -*-
"""三轨状态枚举（F-02 引入）。

职责：定义 UI/API/DB 三轨校验状态的唯一枚举与构造校验。
- PASS   = 已执行且通过（必须带非空 evidence_ref）
- FAIL   = 已执行但失败（必须带非空 evidence_ref）
- UNKNOWN = 未执行/未发起调用（evidence_ref 为空是合法的）

设计动机（PRD F-02）：
- 历史实现三轨恒为布尔且无 UNKNOWN —— Agent 路径未发起 API 调用也硬赋 True，
  导致「未验证=通过」的假 PASS。
- 枚举后，「未执行即 UNKNOWN」成为显式状态，可被门禁/报告正确消费。

通用性：纯枚举 + 校验函数，不依赖任何业务/系统/行业知识。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

_log = logging.getLogger(__name__)


class TrackStatus(str, Enum):
    """三轨状态枚举。"""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_bool(cls, passed: bool | None) -> "TrackStatus":
        """布尔 → 枚举（历史兼容；None 视为 UNKNOWN）。"""
        if passed is None:
            return cls.UNKNOWN
        return cls.PASS if passed else cls.FAIL

    @classmethod
    def parse(cls, raw: Any) -> "TrackStatus":
        """宽松解析：接受枚举/字符串/布尔。"""
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bool):
            return cls.from_bool(raw)
        s = str(raw or "").strip().upper()
        for status in cls:
            if status.value == s:
                return status
        return cls.UNKNOWN


def validate_track_status(track_name: str, track_result: dict[str, Any]) -> None:
    """构造期校验：非 UNKNOWN 轨必须带非空 evidence_ref（PRD F-02③）。

    违规 → 抛 ValueError（fail-fast，防止「通过/失败但无证据」入库）。
    """
    if not isinstance(track_result, dict):
        raise ValueError(
            f"轨 {track_name} 结果必须为 dict，收到 {type(track_result).__name__}"
        )

    status = TrackStatus.parse(track_result.get("status"))
    evidence_ref = track_result.get("evidence_ref")
    if status in (TrackStatus.PASS, TrackStatus.FAIL) and not evidence_ref:
        raise ValueError(
            f"轨 {track_name} 状态={status.value} 但 evidence_ref 为空 —— "
            "通过/失败必须带证据（PRD F-02③，fail-fast）"
        )


def normalize_track(track_name: str, track_result: dict[str, Any]) -> dict[str, Any]:
    """归一化单轨结果：确保含 status 字段（缺省按 passed 布尔推断），并执行构造校验。

    Args:
        track_name: "ui" / "api" / "db"
        track_result: 轨结果字典（含 passed / evidence / evidence_ref 等）

    Returns:
        带 status 字段的轨结果副本。

    Raises:
        ValueError: 非 UNKNOWN 轨缺 evidence_ref。
    """
    result = dict(track_result or {})
    if "status" not in result:
        result["status"] = TrackStatus.from_bool(result.get("passed")).value
    # 向下兼容：未显式提供 evidence_ref 时，用 evidence 文本兜底（非空即视为有证据）
    if not result.get("evidence_ref") and result.get("evidence"):
        result["evidence_ref"] = str(result["evidence"])[:500]
    validate_track_status(track_name, result)
    return result
