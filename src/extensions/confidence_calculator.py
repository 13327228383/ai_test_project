# -*- coding: utf-8 -*-
"""
置信度计算器 (ConfidenceCalculator) — facade
=====================================
基于多因子计算测试用例执行结果的可信度。

设计原则：
  - 不依赖 AI 自我评分，用客观指标计算
  - 多因子加权，每个因子可独立验证
  - 置信度分层：>90% 自动通过，70-90% 建议复核，<70% 需人工确认

置信度公式：
  confidence = (
    assertion_rate * 0.35 +      # 断言通过率（最客观）— 从 evidence_items / checks[] 验证结果计算
    case_completeness * 0.25 +  # 用例完整度（核心字段填充率，替代旧 structured_rate）
    hard_verify_rate * 0.20 +    # 硬验证通过率（防幻觉）
    semantic_align * 0.15 +      # 语义对齐度（需求-测试用例匹配）
    cache_hit_rate * 0.05        # 经验复用命中率（op_cache + capability_cache）
  )

使用位置：Step4 执行完成后 → 计算置信度 → 写入报告

本模块为 facade，实际实现拆分至 ``confidence_calculator_mixins/``：
  - ``_meta``:     _DualMethod 描述符 + _ConfidenceMeta 元类
  - ``_calculate``: 权重管理 + 单条置信度计算
  - ``_block``:    低置信度拦截 + 批量计算
  - ``_feedback``: QA 反馈闭环
  - ``_trend``:    跨版本趋势告警

外部导入路径保持不变：``from src.extensions.confidence_calculator import ConfidenceCalculator``
"""

import logging
from typing import Any
from .confidence_calculator_mixins._meta import _DualMethod, _ConfidenceMeta
from .confidence_calculator_mixins._calculate import ConfidenceCalculatorCalculateMixin
from .confidence_calculator_mixins._block import ConfidenceCalculatorBlockMixin
from .confidence_calculator_mixins._feedback import ConfidenceCalculatorFeedbackMixin
from .confidence_calculator_mixins._trend import ConfidenceCalculatorTrendMixin

_log = logging.getLogger("confidence")


class ConfidenceCalculator(
    ConfidenceCalculatorCalculateMixin,
    ConfidenceCalculatorBlockMixin,
    ConfidenceCalculatorFeedbackMixin,
    ConfidenceCalculatorTrendMixin,
    metaclass=_ConfidenceMeta,
):
    """多因子置信度计算器。★ P2-03: 权重改为实例变量，避免多线程竞争。"""

    # ── ★ P2-03: 类方法转发（向后兼容，通过共享单例委托） ──
    _instance: "ConfidenceCalculator" = None

    @classmethod
    def _get_instance(cls) -> "ConfidenceCalculator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def calculate(cls, intent_result: dict[str, Any]) -> dict[str, Any]:
        return cls._get_instance()._calculate(intent_result)

    @classmethod
    def should_block(cls, intent_result: dict[str, Any]) -> dict[str, Any]:
        return cls._get_instance()._should_block(intent_result)

    @classmethod
    def filter_low_confidence(
        cls, intent_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return cls._get_instance()._filter_low_confidence(intent_results)

    @classmethod
    def calculate_batch(cls, intent_results: list[dict[str, Any]]) -> dict[str, Any]:
        return cls._get_instance()._calculate_batch(intent_results)

    @classmethod
    def record_feedback(cls, case_id: str, qa_decision: str, qa_notes: str = ""):
        return cls._get_instance()._record_feedback(case_id, qa_decision, qa_notes)

    @classmethod
    def get_feedback_stats(cls) -> dict[str, Any]:
        return cls._get_instance()._get_feedback_stats()

    @classmethod
    def calibrate_weights_from_feedback(cls) -> dict[str, float]:
        return cls._get_instance()._calibrate_weights_from_feedback()

    @classmethod
    def check_confidence_trend(
        cls, current_avg: float, project_name: str = None
    ) -> dict[str, Any]:
        return cls._get_instance()._check_confidence_trend(current_avg, project_name)


# ★ P2-03 深度修复：用双态描述符覆盖同名 @classmethod，避免无限递归，并保留实例级权重隔离。
ConfidenceCalculator.calculate = _DualMethod(ConfidenceCalculator._calculate)
ConfidenceCalculator.get_weights = _DualMethod(ConfidenceCalculator.get_weights)
ConfidenceCalculator.set_weights = _DualMethod(ConfidenceCalculator.set_weights)
ConfidenceCalculator.should_block = _DualMethod(ConfidenceCalculator._should_block)
ConfidenceCalculator.filter_low_confidence = _DualMethod(
    ConfidenceCalculator._filter_low_confidence
)
ConfidenceCalculator.calculate_batch = _DualMethod(
    ConfidenceCalculator._calculate_batch
)
ConfidenceCalculator.record_feedback = _DualMethod(
    ConfidenceCalculator._record_feedback
)
ConfidenceCalculator.get_feedback_stats = _DualMethod(
    ConfidenceCalculator._get_feedback_stats
)
ConfidenceCalculator.calibrate_weights_from_feedback = _DualMethod(
    ConfidenceCalculator._calibrate_weights_from_feedback
)
ConfidenceCalculator.check_confidence_trend = _DualMethod(
    ConfidenceCalculator._check_confidence_trend
)
