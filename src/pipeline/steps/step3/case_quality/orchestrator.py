# -*- coding: utf-8 -*-
"""★ S3-014: 三层质量保障统一编排器
==================================
规格设计的三层质量保障体系（Few-Shot→交叉验证→去重）统一编排入口。

层级与执行时机：
  1. Few-Shot      — 生成前：在 system_prompt 中注入历史高质量示例（_build_common_contexts）
  2. 交叉验证       — 生成中：高风险需求走双模型取交集（generate_with_cross_validation）
  3. 去重          — 写入前：语义相似度去重（deduplicate_intents）

本模块提供以下编排函数：
  - apply_batch_dedup: 批量写入前去重（带追踪信息）
  - should_run_cross_validation: 判断是否需要交叉验证
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


def apply_batch_dedup(
    new_intents: list[dict], existing_intents: list[dict], threshold: float = None
) -> dict[str, Any]:
    """★ S3-014: 批量写入前去重 — 四层质量保障的第 4 层。

    调用 deduplicate_intents 进行语义去重，返回追踪信息。

    Args:
        new_intents: 新生成的用例列表
        existing_intents: 已有的用例列表（DB 中）
        threshold: 相似度阈值（None 时走 pipeline_config）

    Returns:
        {
            "kept": list,           # 保留的用例
            "duplicates": list,     # 被标记为重复的用例（含 _dedup_action 标记）
            "duplicate_count": int, # 重复数
            "total_input": int,     # 输入总数
            "dedup_applied": bool,  # 去重是否执行
        }
    """
    total_input = len(new_intents)

    if not new_intents or not existing_intents:
        return {
            "kept": new_intents,
            "duplicates": [],
            "duplicate_count": 0,
            "total_input": total_input,
            "dedup_applied": False,
        }

    try:
        from src.pipeline.steps.step3.case_quality.dedup import deduplicate_intents

        result = deduplicate_intents(
            new_intents, existing_intents, threshold=threshold, return_tracking=True
        )
        result["total_input"] = total_input
        result["dedup_applied"] = True
        if result["duplicate_count"] > 0:
            _log.info(
                "[QA-Orchestrator] 去重: %d/%d 用例被标记为重复",
                result["duplicate_count"],
                total_input,
            )
        return result
    except Exception as e:
        _log.warning("[QA-Orchestrator] 去重异常(不阻塞): %s", str(e)[:120])
        return {
            "kept": new_intents,
            "duplicates": [],
            "duplicate_count": 0,
            "total_input": total_input,
            "dedup_applied": False,
        }


def should_run_cross_validation(risk_level: str) -> bool:
    """★ S3-014: 判断是否需要交叉验证（基于可配置阈值）。

    阈值层级：
      - "high":   仅 high 风险触发（默认，成本最优）
      - "medium": medium + high 风险触发
      - "low":    全部触发（成本最高）

    Args:
        risk_level: 需求风险等级 (high|medium|low)

    Returns:
        bool: 是否触发交叉验证
    """
    try:
        from src.core.pipeline_config import pipeline_config

        threshold = getattr(pipeline_config, "cross_validation_risk_threshold", "high")
    except Exception:
        threshold = "high"

    threshold = (threshold or "high").lower()
    risk_level = (risk_level or "medium").lower()

    if threshold == "low":
        return True  # 全部触发
    if threshold == "medium":
        return risk_level in ("medium", "high")
    # 默认 "high"
    return risk_level == "high"
