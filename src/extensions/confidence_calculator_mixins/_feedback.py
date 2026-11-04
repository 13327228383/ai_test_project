# -*- coding: utf-8 -*-
"""
置信度计算器 —— QA 反馈闭环 mixin
=====================================
QA 标注反馈记录 + 统计 + 权重自动校准。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不变。

★ MySQL 化：QA 反馈已从 JSONL 文件迁移至 MySQL qa_feedback 表
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger("confidence")


class ConfidenceCalculatorFeedbackMixin:
    """QA 反馈闭环 mixin：反馈记录 + 统计 + 权重校准。"""

    # ═══════════════════════════════════════════════════════════════
    # P1-2 QA 标注反馈闭环
    # ═══════════════════════════════════════════════════════════════
    # ★ MySQL 化：QA 反馈已从 JSONL 文件迁移至 MySQL qa_feedback 表

    @staticmethod
    def _get_project() -> str:
        """获取当前项目名（统一来源，消除双重上下文不一致）。

        优先级：state._current_project()（与 dashboard 同源）→
                config.project_name（ConfigRegistry 回退）→
                空字符串。
        ★ 修复(2026-08-14): 原硬编码 "default_project" 兜底，导致项目上下文丢失时
          QA 反馈静默写入错误 scope。
        """
        with guard("_get_project: from src.backend.state import _curre", log=_log):
            from src.backend.state import _current_project

            proj = _current_project()
            if proj:
                return proj
        try:
            from src.core.config_loader import config

            return getattr(config, "project_name", "") or ""
        except Exception:
            return ""

    def record_feedback(self, case_id: str, qa_decision: str, qa_notes: str = ""):
        return self._record_feedback(case_id, qa_decision, qa_notes)

    def record_global_feedback(
        self, case_id: str, qa_decision: str, qa_notes: str = ""
    ):
        """记录可跨项目复用的全局反馈模式。"""
        return self._record_feedback(
            case_id, qa_decision, qa_notes, scope_type="global", scope_id=""
        )

    def _record_feedback(
        self,
        case_id: str,
        qa_decision: str,
        qa_notes: str = "",
        scope_type: str = "project",
        scope_id: str = None,
    ):
        """记录一条 QA 标注反馈到 MySQL qa_feedback 表。
        qa_decision ∈ {"correct", "false_positive", "false_negative", "needs_review"}
        """
        try:
            if qa_decision not in {
                "correct",
                "false_positive",
                "false_negative",
                "needs_review",
            }:
                return False
            from src.core.db_manager import get_connection, release_connection

            conn = get_connection()
            try:
                if scope_id is None:
                    scope_id = self._get_project() if scope_type == "project" else ""
                conn.execute(
                    "INSERT INTO qa_feedback(scope_type, scope_id, case_id, qa_decision, qa_notes) "
                    "VALUES(%s, %s, %s, %s, %s)",
                    (scope_type, scope_id, case_id, qa_decision, qa_notes),
                )
                conn.commit()
                self._feedback_count += 1
                return True
            finally:
                release_connection(conn)
        except Exception as e:
            _log.warning("QA反馈写入异常: %s", e, exc_info=True)
            return False

    def get_feedback_stats(self) -> dict[str, Any]:
        return self._get_feedback_stats()

    def _get_feedback_stats(self) -> dict[str, Any]:
        """返回 QA 反馈统计（从 MySQL qa_feedback 表）。"""
        try:
            from src.core.db_manager import get_connection, release_connection

            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT qa_decision, COUNT(*) AS cnt FROM qa_feedback "
                    "WHERE (scope_type='global' AND scope_id='') OR (scope_type='project' AND scope_id=%s) GROUP BY qa_decision",
                    (self._get_project(),),
                ).fetchall()
                counts = {r["qa_decision"]: r["cnt"] for r in rows}
                total = sum(counts.values())
                correct = counts.get("correct", 0)
                fp = counts.get("false_positive", 0)
                fn = counts.get("false_negative", 0)
                needs_review = counts.get("needs_review", 0)
                precision = correct / max(correct + fp, 1)
                recall = correct / max(correct + fn, 1)
                return {
                    "total": total,
                    "correct": correct,
                    "fp": fp,
                    "fn": fn,
                    "needs_review": needs_review,
                    "precision": round(precision, 3),
                    "recall": round(recall, 3),
                }
            finally:
                release_connection(conn)
        except Exception as e:
            _log.warning("反馈统计查询异常: %s", e, exc_info=True)
            return {
                "total": 0,
                "correct": 0,
                "fp": 0,
                "fn": 0,
                "needs_review": 0,
                "precision": 0.0,
                "recall": 0.0,
            }

    def calibrate_weights_from_feedback(self) -> dict[str, float]:
        return self._calibrate_weights_from_feedback()

    def _calibrate_weights_from_feedback(self) -> dict[str, float]:
        """基于 QA 反馈统计自动校准权重：
        - 每 20 条反馈触发一次校准（频率控制）
        - 如果 correct 占比 < 60%，提升 assertion_rate 权重，降低 semantic_align 权重，
          其余权重按比例调整
        返回新权重 dict。
        """
        try:
            stats = self.get_feedback_stats()
            total = stats.get("total", 0)
            # 频率控制：每累计 20 条新反馈才触发一次校准
            if total - self._last_calibration_count < 20:
                return self.get_weights()
            self._last_calibration_count = total
            correct = stats.get("correct", 0)
            correct_ratio = correct / max(total, 1)
            # 当前权重：assertion_rate 0.35, semantic_align 0.15, 其余 0.50
            # 计划变化：assertion_rate +0.10, semantic_align -0.10, 其余按比例缩放
            adjust = 0.10 if correct_ratio <= 0.60 else -0.10
            weights = self.get_weights()
            current_assertion = weights["assertion_rate"]
            current_semantic = weights["semantic_align"]
            rest_total = 1.0 - current_assertion - current_semantic
            new_assertion = max(0.05, min(0.75, current_assertion + adjust))
            new_semantic = max(0.02, min(0.45, current_semantic - adjust))
            # 按比例调整其他三项
            new_rest_total = 1.0 - new_assertion - new_semantic
            scale = new_rest_total / max(rest_total, 1e-9)
            new_weights = {
                "assertion_rate": round(new_assertion, 3),
                "semantic_align": round(new_semantic, 3),
            }
            for key in (
                "case_completeness",
                "hard_verify_rate",
                "first_try_rate",
                "cache_hit_rate",
            ):
                new_weights[key] = round(weights[key] * scale, 3)
            # 归一化，避免浮点误差导致和不为 1
            s = sum(new_weights.values())
            if s > 0:
                for k in new_weights:
                    new_weights[k] = round(new_weights[k] / s, 3)
            self.set_weights(new_weights)
            return dict(new_weights)
        except Exception:
            return self.get_weights()
