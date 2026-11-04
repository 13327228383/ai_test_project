# -*- coding: utf-8 -*-
"""
置信度计算器 —— 拦截判定 mixin
=====================================
低置信度拦截 + 批量扫描 + 批量置信度计算。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不变。

使用位置：Step4 执行完成后 → 计算置信度 → 写入报告
"""

from typing import Any
from src.core.pipeline_config import _read_config_value as _cfg


class ConfidenceCalculatorBlockMixin:
    """拦截判定 mixin：低置信度拦截 + 批量计算。"""

    @property
    def LOW_CONFIDENCE_THRESHOLD(self):
        return float(_cfg("confidence_low_threshold", 0.50))

    @property
    def MEDIUM_CONFIDENCE_THRESHOLD(self):
        return float(_cfg("confidence_medium_threshold", 0.70))

    @property
    def HIGH_CONFIDENCE_THRESHOLD(self):
        return float(_cfg("confidence_high_threshold", 0.90))

    def should_block(self, intent_result: dict[str, Any]) -> dict[str, Any]:
        return self._should_block(intent_result)

    def _should_block(self, intent_result: dict[str, Any]) -> dict[str, Any]:
        """判定某条测试用例是否因置信度过低需要拦截（不允许自动通过）。

        返回: { "block": bool, "reason": str, "confidence": float, "level": str }
        """
        calc = self.calculate(intent_result)
        confidence = calc["confidence"]
        level = calc["level"]
        # 硬性拦截：未通过 或 置信度低于 50%
        if not intent_result.get("passed", False):
            # ★ 规则生成用例豁免门禁（passed=False 不拦截，仅记录失败）
            if intent_result.get("_generation_source") == "rule_extract":
                return {
                    "block": False,
                    "reason": "规则生成用例执行失败（豁免门禁）",
                    "confidence": confidence,
                    "level": "low",
                }
            return {
                "block": True,
                "reason": "测试用例执行失败，必须人工复核",
                "confidence": confidence,
                "level": level,
            }
        if confidence < self.LOW_CONFIDENCE_THRESHOLD:
            reasons = []
            factors = calc.get("factors", {})
            if factors.get("assertion_rate", 0) < 0.5:
                reasons.append("断言通过率过低")
            if factors.get("hard_verify_rate", 0) < 0.3:
                reasons.append("硬验证缺失（疑似幻觉）")
            if factors.get("first_try_rate", 0) < 0.4:
                reasons.append(
                    "AI 反复重试才勉强通过（%s）" % calc.get("avg_rounds", "?")
                )
            if factors.get("case_completeness", 0) < 0.3:
                reasons.append("意图字段不完整")
            if factors.get("semantic_align", 0) < 0.3:
                reasons.append("需求-测试用例语义不匹配")
            reason = "置信度 %.0f%% < 50%% 阈值，原因：%s" % (
                confidence * 100,
                "、".join(reasons) or "多因子整体偏低",
            )
            return {
                "block": True,
                "reason": reason,
                "confidence": confidence,
                "level": level,
            }
        # 软性提示：70% 以下只提示，不强制拦截
        if confidence < self.MEDIUM_CONFIDENCE_THRESHOLD:
            return {
                "block": False,
                "reason": "置信度偏低 (%.0f%%)，建议人工复核" % (confidence * 100),
                "confidence": confidence,
                "level": level,
            }
        return {
            "block": False,
            "reason": "置信度达标",
            "confidence": confidence,
            "level": level,
        }

    def filter_low_confidence(
        self, intent_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._filter_low_confidence(intent_results)

    def _filter_low_confidence(
        self, intent_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """批量扫描，返回被拦截的测试用例列表。"""
        blocked, warned = [], []
        for cr in intent_results:
            info = self.should_block(cr)
            if info["block"]:
                blocked.append(
                    {
                        "case_id": cr.get("case_id", "?"),
                        "title": cr.get("title", ""),
                        **info,
                    }
                )
            elif info.get("level") == "medium":
                warned.append(
                    {
                        "case_id": cr.get("case_id", "?"),
                        "title": cr.get("title", ""),
                        **info,
                    }
                )
        return {
            "blocked": blocked,
            "warned": warned,
            "block_rate": round(len(blocked) / max(len(intent_results), 1) * 100, 1),
            "warn_rate": round(len(warned) / max(len(intent_results), 1) * 100, 1),
        }

    def calculate_batch(self, intent_results: list[dict[str, Any]]) -> dict[str, Any]:
        return self._calculate_batch(intent_results)

    def _calculate_batch(self, intent_results: list[dict[str, Any]]) -> dict[str, Any]:
        """批量计算置信度，返回汇总报告。"""
        if not intent_results:
            return {
                "avg_confidence": 0.0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "details": [],
            }

        details = [self.calculate(cr) for cr in intent_results]
        avg_conf = (
            sum(d["confidence"] for d in details) / len(details) if details else 0.0
        )

        return {
            "avg_confidence": round(avg_conf, 3),
            "high": sum(1 for d in details if d["level"] == "high"),
            "medium": sum(1 for d in details if d["level"] == "medium"),
            "low": sum(1 for d in details if d["level"] == "low"),
            "details": details,
        }
