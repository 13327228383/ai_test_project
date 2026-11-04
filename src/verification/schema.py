# -*- coding: utf-8 -*-
"""验证台账数据模型与枚举定义（schema）。

★ 职责：定义结构化 `LedgerRecord`（pydantic 校验）+ 全部枚举
  （TruthSource / Consistency / LedgerStatus / Disposition），并提供
  字段级 JSON schema 风格校验，供 LedgerStore / CLI / Runner 复用。

★ 落盘约定：每条记录一行 JSONL，字段命名严格遵循共享知识 §8：
  target / case_id / engine_expected / truth_value / consistent /
  status / disposition / truth_source / truth_detail / owner / sla /
  approved_by / updated_at。

★ 依赖方向：仅依赖标准库 + pydantic，不依赖 pipeline/empirical。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# ─────────────────────────────────────────────────────────────────────
# 枚举定义（与共享知识 §8 严格对齐）
# ─────────────────────────────────────────────────────────────────────


class TruthSource(str, Enum):
    """独立真值来源类型。

    - HUMAN: 人工标注
    - SPEC:  业务规格（靶子 spec.md）
    - DOC:   规格文档
    """

    HUMAN = "human"
    SPEC = "spec"
    DOC = "doc"


class Consistency(str, Enum):
    """引擎预期与独立真值的一致性结论。

    - TRUE:             一致
    - FALSE:            不一致
    - TRUTH_UNAVAILABLE: 真值缺失（绝不静默通过）
    """

    TRUE = "true"
    FALSE = "false"
    TRUTH_UNAVAILABLE = "truth_unavailable"


class LedgerStatus(str, Enum):
    """补做台账状态枚举。

    合法转移见 RemediationStateMachine：
      OPEN -> FIXING -> RECHECK -> CLOSED | OPEN
      OPEN -> KNOWN_LIMITATION -> CLOSED（guarded: approved_by 非空）
    """

    OPEN = "open"
    FIXING = "fixing"
    RECHECK = "recheck"
    CLOSED = "closed"
    KNOWN_LIMITATION = "known_limitation"


class Disposition(str, Enum):
    """处置口径枚举。"""

    FIX_CASE = "fix_case"
    FIX_TARGET = "fix_target"
    KNOWN_LIMITATION = "known_limitation"
    NONE = "none"


# ─────────────────────────────────────────────────────────────────────
# 枚举值白名单（用于自定义校验器，避免 pydantic 自动归一化误差）
# ─────────────────────────────────────────────────────────────────────
_TRUTH_SOURCE_VALUES = {e.value for e in TruthSource}
_CONSISTENCY_VALUES = {e.value for e in Consistency}
_STATUS_VALUES = {e.value for e in LedgerStatus}
_DISPOSITION_VALUES = {e.value for e in Disposition}


def _now_iso() -> str:
    """返回当前 UTC 的 ISO8601 字符串（无微秒、带 Z）。"""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ─────────────────────────────────────────────────────────────────────
# LedgerRecord 数据模型
# ─────────────────────────────────────────────────────────────────────


class LedgerRecord(BaseModel):
    """验证台账单条记录（pydantic 校验）。

    字段说明（共享知识 §8）：
      target:        靶子 key（edu_sut / fin_sut / hospital_sut ...）
      case_id:       用例 ID（如 P01 / T-转账-01）
      engine_expected: 引擎跑完后的预期（状态码/业务体 dict）
      truth_value:   独立真值（人工/规格 dict），缺失时为 None
      consistent:    一致性结论（true/false/truth_unavailable）
      status:        补做状态（open/fixing/recheck/closed/known_limitation）
      disposition:   处置口径（fix_case/fix_target/known_limitation/none）
      truth_source:  真值来源（human/spec/doc）
      truth_detail:  真值说明/出处
      owner:         补做负责人（open 时指派）
      sla:           预期解决时限 ISO8601
      approved_by:   known_limitation 关闭审批人（空=未审批）
      updated_at:    ISO8601 UTC 更新时间
    """

    target: str = Field(..., min_length=1)
    case_id: str = Field(..., min_length=1)
    engine_expected: dict[str, Any] = Field(default_factory=dict)
    truth_value: Optional[dict[str, Any]] = None
    consistent: str = Field(default=Consistency.TRUTH_UNAVAILABLE.value)
    status: str = Field(default=LedgerStatus.OPEN.value)
    disposition: str = Field(default=Disposition.NONE.value)
    truth_source: Optional[str] = None
    truth_detail: str = Field(default="")
    owner: str = Field(default="")
    sla: str = Field(default="")
    approved_by: str = Field(default="")
    updated_at: str = Field(default_factory=_now_iso)

    # ── 自定义校验：枚举值白名单 ──
    @field_validator("consistent")
    @classmethod
    def _check_consistent(cls, v: str) -> str:
        if v not in _CONSISTENCY_VALUES:
            raise ValueError(
                f"consistent 必须为 {sorted(_CONSISTENCY_VALUES)}，收到 {v!r}"
            )
        return v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in _STATUS_VALUES:
            raise ValueError(
                f"status 必须为 {sorted(_STATUS_VALUES)}，收到 {v!r}"
            )
        return v

    @field_validator("disposition")
    @classmethod
    def _check_disposition(cls, v: str) -> str:
        if v not in _DISPOSITION_VALUES:
            raise ValueError(
                f"disposition 必须为 {sorted(_DISPOSITION_VALUES)}，收到 {v!r}"
            )
        return v

    @field_validator("truth_source")
    @classmethod
    def _check_truth_source(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _TRUTH_SOURCE_VALUES:
            raise ValueError(
                f"truth_source 必须为 {sorted(_TRUTH_SOURCE_VALUES)} 或 None，"
                f"收到 {v!r}"
            )
        return v

    # ── 序列化 / 反序列化 ──
    def to_dict(self) -> dict[str, Any]:
        """转为可落盘 dict（保持字段顺序，便于 JSONL 人读）。"""
        return {
            "target": self.target,
            "case_id": self.case_id,
            "engine_expected": self.engine_expected,
            "truth_value": self.truth_value,
            "consistent": self.consistent,
            "status": self.status,
            "disposition": self.disposition,
            "truth_source": self.truth_source,
            "truth_detail": self.truth_detail,
            "owner": self.owner,
            "sla": self.sla,
            "approved_by": self.approved_by,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LedgerRecord":
        """从 dict 构造（容忍多余键，缺失键走默认值）。"""
        known = set(cls.model_fields.keys())
        payload = {k: v for k, v in d.items() if k in known}
        return cls(**payload)


def json_schema() -> dict[str, Any]:
    """返回字段级 JSON schema（供 CLI / 外部校验工具使用）。

    Returns:
        JSON Schema dict，描述 LedgerRecord 全部字段类型与枚举约束。
    """
    return {
        "type": "object",
        "required": ["target", "case_id", "engine_expected", "consistent", "status"],
        "properties": {
            "target": {"type": "string", "minLength": 1},
            "case_id": {"type": "string", "minLength": 1},
            "engine_expected": {"type": "object"},
            "truth_value": {"type": ["object", "null"]},
            "consistent": {
                "type": "string",
                "enum": sorted(_CONSISTENCY_VALUES),
            },
            "status": {"type": "string", "enum": sorted(_STATUS_VALUES)},
            "disposition": {
                "type": "string",
                "enum": sorted(_DISPOSITION_VALUES),
            },
            "truth_source": {
                "type": ["string", "null"],
                "enum": sorted(_TRUTH_SOURCE_VALUES) + [None],
            },
            "truth_detail": {"type": "string"},
            "owner": {"type": "string"},
            "sla": {"type": "string"},
            "approved_by": {"type": "string"},
            "updated_at": {"type": "string"},
        },
    }
