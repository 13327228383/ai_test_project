# -*- coding: utf-8 -*-
from __future__ import annotations

"""意图质量评估工具（从 step3.py 拆分）。

★ 流水线强化专项 T2（2026-08-24）：输出并入 methodology_validation 小节
  （只增键不删键，对齐 design §7.6），由 MethodologyValidator.merge_into_quality_summary
  统一装配；本函数保持向后兼容（methodology_validation 缺省为空 dict）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


def summarize_case_modeling_quality(
    confidence_scores: dict[str, Any],
    validation_warnings=None,
    generated_count: int = 0,
    methodology_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总单个需求本次建模测试用例的质量指标。

    Args:
        confidence_scores: 置信度分数 dict（case_id → score）。
        validation_warnings: 建模校验告警列表（可选）。
        generated_count: 生成的用例数。
        methodology_validation: ★T2 方法论白名单校验结果 dict（可选）；
            传入后并入输出的 ``methodology_validation`` 小节。

    Returns:
        质量摘要 dict（含 generated_count / avg_quality_score /
        low_quality_count / validation_warning_count；T2 增加 methodology_validation）。
    """
    scores = []
    for value in (confidence_scores or {}).values():
        try:
            scores.append(float(value))
        except Exception as _e:
            _log.debug(
                "[_intent_quality] summarize_case_modeling_quality 异常: %s",
                _e,
                exc_info=True,
            )
            continue
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    low_count = sum(1 for s in scores if s < 60) if scores else 0
    warn_count = (
        len(validation_warnings or []) if isinstance(validation_warnings, list) else 0
    )
    summary: dict[str, Any] = {
        "generated_count": int(generated_count or 0),
        "avg_quality_score": avg_score,
        "low_quality_count": low_count,
        "validation_warning_count": warn_count,
    }
    # ★ T2: 并入方法论白名单校验结果（只增键不删键）
    if methodology_validation:
        from src.pipeline.step3._methodology_validator import MethodologyValidator

        summary = MethodologyValidator.merge_into_quality_summary(
            summary, methodology_validation
        )
    else:
        summary["methodology_validation"] = {}
    return summary
