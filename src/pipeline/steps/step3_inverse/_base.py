# -*- coding: utf-8 -*-
from __future__ import annotations

"""共享数据类与反向意图构造工厂。

职责：保证三个反向维度（replay / security / ordering）输出的意图结构与
step3 正向意图完全一致——能被 step4._execute_structured_actions 直接消费。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import logging

_log = logging.getLogger(__name__)


@dataclass
class InverseGenContext:
    """反向覆盖生成的上下文，用于跨意图的 ID 计数器、统计等。"""

    case_id_counter: int = 0
    generated_count_by_dim: dict[str, int] = field(default_factory=dict)

    def next_case_id(self, dim: str, source_id: str) -> str:
        """生成形如 INV-replay-001-FROM-TC-005 的反向意图 ID。"""
        self.case_id_counter += 1
        self.generated_count_by_dim[dim] = self.generated_count_by_dim.get(dim, 0) + 1
        seq = self.generated_count_by_dim[dim]
        return f"INV-{dim}-{seq:03d}-FROM-{source_id}"


def build_inverse_intent(
    *,
    source_intent: dict[str, Any],
    dimension: str,
    sub_kind: str,
    title_suffix: str,
    description: str,
    steps: list[dict[str, Any]],
    risk_points: list[str] | None = None,
    expected_defense: str | None = None,
    ctx: InverseGenContext,
) -> dict[str, Any]:
    """从一个正向意图衍生出一个反向意图。

    v3.0: 输出字段与正向用例一致（case_id / title / type / risk_level /
    required_tracks / checks / actions / steps / case_meta / created_date），
    确保可以直接被 step4 主流程消费。

    参数:
        source_intent:    正向意图（v9 5 字段格式）
        dimension:        replay / security / ordering
        sub_kind:         具体子类型（如 idempotency / privilege_escalation /
                          step_skip）—— 写入 case_meta.inverse_kind 供报表筛选
        title_suffix:     反向意图标题后缀（如 "[重放×3]"）
        description:      意图描述文本
        steps:            结构化步骤列表（必须使用 step4 已支持的 action 类型：
                          click / fill / select / navigate / upload / assert / verify）
        risk_points:      反向覆盖关注的风险点
        expected_defense: 预期的防御行为（如 "出现限频提示" / "返回 403"）
        ctx:              生成上下文
    """
    src_id = source_intent.get("case_id", "TC-UNKNOWN")
    # v3.0: source_intent 自身即为 case（不再有 intent_schema 子对象）
    src_title = source_intent.get("title") or src_id
    src_what = source_intent.get("title") or src_title
    src_type = source_intent.get("type") or ""
    src_module = source_intent.get("module") or source_intent.get("location") or ""

    new_id = ctx.next_case_id(dimension, src_id)

    # ---------- title：5 核心字段 + 反向语义重写 ----------
    inverse_what = f"[反向-{dimension}] {src_what} — 预期防御: {expected_defense}"
    inverse_risk = (
        "P0" if dimension == "security" else source_intent.get("risk_level") or "P1"
    )
    inverse_tracks = list(source_intent.get("required_tracks") or ["ui"])

    # ---------- checks[]：保留正向的，反向测试也要看 UI/API/DB 落点 ----------
    checks = source_intent.get("checks") or []
    # 兼容 JSON 字符串
    if isinstance(checks, str):
        try:
            import json as _json

            checks = _json.loads(checks)
        except Exception as _e:
            _log.debug("[_base] 非关键异常: %s", _e, exc_info=True)
            checks = []
    if not isinstance(checks, list):
        checks = []

    # ---------- case_meta：标记反向溯源信息 ----------
    inverse_meta = {
        "title": f"{src_title} {title_suffix}",
        "case_type": f"反向-{dimension}",
        "inverse_kind": sub_kind,  # idempotency / xss / step_skip / ...
        "source_case_id": src_id,
        "risk_level": inverse_risk,
    }

    return {
        "case_id": new_id,
        "created_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "title": f"{src_title} {title_suffix}",
        "title": inverse_what,
        "type": "反向覆盖",
        "risk_level": inverse_risk,
        "required_tracks": inverse_tracks,
        "checks": checks,
        "case_meta": inverse_meta,
        "case_description": description,
        "steps": steps,
        "_inverse_generated": True,
    }


__all__ = ["InverseGenContext", "build_inverse_intent"]
