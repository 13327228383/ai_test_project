# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
ScorecardOracle 插件 — 评分卡判定机制
======================================
解决 P0-3: 二元断言导致 Flaky Tests 泛滥。

核心能力：
- 加权评分卡：将结果分解为多个维度，加权评分
- 动态阈值：根据历史数据调整通过阈值
- 置信度量化：输出 0-100 的置信度分数，而非简单 True/False
- 多维度评估：功能性、稳定性、性能、视觉一致性

介入点：ON_VERIFY / AFTER_STEP
"""

import logging
from typing import Any
from datetime import datetime

_log = logging.getLogger(__name__)


class ScorecardOraclePlugin:
    """评分卡判定插件。"""

    name = "scorecard_oracle"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        # 尝试从全局配置读取默认维度与阈值（config.yaml 中 step4_plugins.scorecard_oracle 已定义）
        # 注意：ConfigLoader 将 YAML 平铺为 UPPERCASE，嵌套结构需从 _yaml_raw 读取
        _global_dims = None
        _global_threshold = None
        with guard("__init__: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config as _cfg

            _plugin_cfg = (
                _cfg._yaml_raw.get("step4_plugins", {})
                if hasattr(_cfg, "_yaml_raw")
                else {}
            )
            if isinstance(_plugin_cfg, dict):
                _oracle_cfg = _plugin_cfg.get("scorecard_oracle", {}) or {}
                if isinstance(_oracle_cfg, dict):
                    _global_dims = _oracle_cfg.get("dimensions")
                    _global_threshold = _oracle_cfg.get("overall_threshold")

        # 评分维度配置：插件级 > 全局配置 > 硬编码兜底
        self._dimensions = self.config.get(
            "dimensions",
            _global_dims
            or {
                "functional": {"weight": 0.4, "threshold": 0.8},  # 功能正确性
                "stability": {"weight": 0.2, "threshold": 0.7},  # 稳定性（重试一致性）
                "performance": {"weight": 0.2, "threshold": 0.6},  # 性能指标
                "visual": {"weight": 0.1, "threshold": 0.7},  # 视觉一致性
                "data_integrity": {"weight": 0.1, "threshold": 0.9},  # 数据完整性
            },
        )

        # 全局通过阈值
        self._pass_threshold = self.config.get(
            "pass_threshold",
            _global_threshold if _global_threshold is not None else 0.75,
        )

        # 历史数据（用于动态阈值调整）
        self._history: list[dict[str, Any]] = []
        self._max_history = self.config.get("max_history", 100)

        # 统计
        self._stats = {
            "total_evaluations": 0,
            "pass_count": 0,
            "fail_count": 0,
            "borderline_count": 0,  # 边缘情况（阈值 ±5%）
        }

    def on_verify(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        验证时：使用评分卡替代二元断言。

        ctx 包含:
            assertion: dict — 断言定义
            actual: any — 实际值
            expected: any — 期望值
            step_result: dict — 步骤执行结果
        """
        if not self.enabled:
            return None

        step_result = ctx.get("step_result", {})

        # 构建评分卡
        scorecard = self._evaluate_scorecard(step_result, ctx)

        # 判定结果
        total_score = scorecard.get("total_score", 0)
        passed = total_score >= self._pass_threshold

        # 边缘情况检测
        is_borderline = abs(total_score - self._pass_threshold) <= 0.05

        self._stats["total_evaluations"] += 1
        if passed:
            self._stats["pass_count"] += 1
        else:
            self._stats["fail_count"] += 1
        if is_borderline:
            self._stats["borderline_count"] += 1

        # 记录历史
        self._history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "score": total_score,
                "passed": passed,
                "dimensions": scorecard.get("dimensions", {}),
            }
        )
        if len(self._history) > self._max_history:
            self._history.pop(0)

        result = {
            # ★ F-03⑨ (2026-08-04): scorecard 仅作诊断输出，物理摘除判定权。
            #   judgment_authority=false 供报告 schema 校验拒绝将本输出写入
            #   confidence/verdict/status（PRD F-03⑨(1)(2) 命名隔离+依赖方向固化）。
            "judgment_authority": False,
            "scorecard_passed": passed,
            "scorecard_score": round(total_score, 4),
            "scorecard_threshold": self._pass_threshold,
            "scorecard_is_borderline": is_borderline,
            "scorecard_dimensions": scorecard.get("dimensions", {}),
        }

        if is_borderline:
            _log.warning(
                "[Scorecard] ⚠ 边缘情况: score=%.2f, threshold=%.2f",
                total_score,
                self._pass_threshold,
            )
            result["scorecard_recommendation"] = "建议人工复核"

        return result

    def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        步骤完成后：综合评分。

        ctx 包含:
            step: dict
            result: bool
            step_result: dict  # _execute_one_intent 分发 after_step 时传入的实际键

        ★ 根因修复(2026-09-20)：on_verify 方法虽含评分+历史累积，但 PluginManager
        从未分发 on_verify 钩子（dispatch 仅覆盖 before/after_step 等），导致 _history
        与 _stats 恒空 → get_stats() 的 avg_score 恒为 0、high/medium/low 全 0。
        真正被接线的是 on_after_step。「根因修复 2」：此方法原读 ctx['step_result_entry']
        ——_base.py 分发 after_step 时从未传该键（实传 step_result），故每次都在空桶
        提前 return，评分状态从未落库（avg_score=0）。现改为读实际键 ctx['step_result']，
        把累积逻辑对齐 on_verify，使评分数据在本方法真实产生。
        """
        if not self.enabled:
            return None

        step_result_entry = ctx.get("step_result", {})
        if not isinstance(step_result_entry, dict) or not step_result_entry:
            # 兼容旧/其它分发方可能仍传 step_result_entry 键
            step_result_entry = ctx.get("step_result_entry", {}) or {}
        if not step_result_entry:
            return None

        # 从步骤结果构建评分卡
        scorecard = self._evaluate_step_scorecard(step_result_entry)
        total_score = scorecard.get("total_score", 0)
        passed = total_score >= self._pass_threshold

        # 边缘情况检测
        is_borderline = abs(total_score - self._pass_threshold) <= 0.05

        # 累积统计 + 历史（与 on_verify 同构，供给 get_stats()/serial scorecard 落库）
        self._stats["total_evaluations"] += 1
        if passed:
            self._stats["pass_count"] += 1
        else:
            self._stats["fail_count"] += 1
        if is_borderline:
            self._stats["borderline_count"] += 1
        self._history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "score": total_score,
                "passed": passed,
                "dimensions": scorecard.get("dimensions", {}),
            }
        )
        if len(self._history) > self._max_history:
            self._history.pop(0)

        return {
            "judgment_authority": False,
            "scorecard_passed": passed,
            "scorecard_score": round(total_score, 4),
            "scorecard_is_borderline": is_borderline,
            "scorecard_dimensions": scorecard.get("dimensions", {}),
        }

    def _evaluate_scorecard(self, step_result: dict, ctx: dict) -> dict[str, Any]:
        """评估评分卡。"""
        dimensions = {}

        # 1. 功能性评分
        func_score = self._score_functional(step_result, ctx)
        dimensions["functional"] = func_score

        # 2. 稳定性评分
        stab_score = self._score_stability(step_result)
        dimensions["stability"] = stab_score

        # 3. 性能评分
        perf_score = self._score_performance(step_result)
        dimensions["performance"] = perf_score

        # 4. 视觉评分
        vis_score = self._score_visual(step_result)
        dimensions["visual"] = vis_score

        # 5. 数据完整性评分
        data_score = self._score_data_integrity(step_result)
        dimensions["data_integrity"] = data_score

        # 加权总分
        total = 0
        for dim_name, dim_config in self._dimensions.items():
            dim_score = dimensions.get(dim_name, 0)
            weight = dim_config.get("weight", 0.2)
            total += dim_score * weight

        return {
            "total_score": total,
            "dimensions": dimensions,
        }

    def _evaluate_step_scorecard(self, step_result_entry: dict) -> dict[str, Any]:
        """从步骤结果条目评估评分卡。"""
        dimensions = {}

        # 功能性：步骤是否通过
        passed = step_result_entry.get("passed", False)
        dimensions["functional"] = 1.0 if passed else 0.0

        # 稳定性：重试次数
        rounds = step_result_entry.get("rounds_needed", 1)
        dimensions["stability"] = max(0, 1.0 - (rounds - 1) * 0.2)

        # 性能：执行耗时
        elapsed = step_result_entry.get("elapsed_ms", 0) or step_result_entry.get(
            "elapsed", 0
        )
        if elapsed > 0:
            # ★ P1: 性能评分阈值走 pipeline_config (env 覆盖)
            try:
                from src.core.pipeline_config import pipeline_config

                _fast = int(pipeline_config.scorecard_performance_fast_threshold)
                _slow = int(pipeline_config.scorecard_performance_slow_threshold)
            except Exception as _e:
                _log.debug("[ScorecardOracle] 读取性能评分阈值失败，使用默认值: %s", _e)
                _fast, _slow = 5000, 30000
            dimensions["performance"] = max(
                0, min(1.0, 1.0 - (elapsed - _fast) / max(1, _slow - _fast))
            )
        else:
            dimensions["performance"] = 0.5

        # 视觉：是否有截图验证
        has_screenshot = bool(step_result_entry.get("screenshot_path"))
        dimensions["visual"] = 1.0 if has_screenshot else 0.5

        # 数据完整性：是否有 DB/API 验证
        has_db_verify = bool(step_result_entry.get("db_verification"))
        has_api_verify = bool(step_result_entry.get("api_verification"))
        dimensions["data_integrity"] = 1.0 if (has_db_verify or has_api_verify) else 0.3

        # 加权总分
        total = 0
        for dim_name, dim_config in self._dimensions.items():
            dim_score = dimensions.get(dim_name, 0)
            weight = dim_config.get("weight", 0.2)
            total += dim_score * weight

        return {
            "total_score": total,
            "dimensions": dimensions,
        }

    def _score_functional(self, step_result: dict, ctx: dict) -> float:
        """功能性评分。"""
        # 基础：步骤是否通过
        if step_result.get("passed", False):
            base = 1.0
        else:
            base = 0.0

        # 如果有断言，检查断言通过率
        assertions = step_result.get("assertions", [])
        if assertions:
            passed_assertions = sum(1 for a in assertions if a.get("passed", False))
            assertion_ratio = passed_assertions / len(assertions)
            # 功能性评分 = 基础 * 0.7 + 断言通过率 * 0.3
            return base * 0.7 + assertion_ratio * 0.3

        return base

    def _score_stability(self, step_result: dict) -> float:
        """稳定性评分。"""
        # 重试次数越少越稳定
        retries = (
            step_result.get("retry_count", 0) or step_result.get("rounds_needed", 1) - 1
        )
        if retries == 0:
            return 1.0
        elif retries == 1:
            return 0.8
        elif retries <= 3:
            return 0.5
        else:
            return 0.2

    def _score_performance(self, step_result: dict) -> float:
        """性能评分。"""
        elapsed = step_result.get("elapsed_ms", 0) or step_result.get("elapsed", 0)
        if elapsed <= 0:
            return 0.5  # 未知

        # 评分曲线：5秒=1.0, 10秒=0.8, 30秒=0.5, 60秒=0.2
        if elapsed <= 5000:
            return 1.0
        elif elapsed <= 10000:
            return 0.8
        elif elapsed <= 30000:
            return 0.5
        elif elapsed <= 60000:
            return 0.2
        else:
            return 0.0

    def _score_visual(self, step_result: dict) -> float:
        """视觉评分。"""
        # 是否有截图验证
        if step_result.get("screenshot_verified", False):
            return 1.0
        if step_result.get("screenshot_path"):
            return 0.7
        if step_result.get("visual_assert_passed", False):
            return 1.0
        return 0.3

    def _score_data_integrity(self, step_result: dict) -> float:
        """数据完整性评分。"""
        # DB 验证
        db_verify = step_result.get("db_verification", {})
        if db_verify.get("passed", False):
            return 1.0
        if db_verify:
            return 0.3  # 有验证但失败

        # API 验证
        api_verify = step_result.get("api_verification", {})
        if api_verify.get("passed", False):
            return 1.0
        if api_verify:
            return 0.3

        return 0.0  # 无数据验证

    def get_stats(self) -> dict[str, Any]:
        """获取评分统计。"""
        total = self._stats["total_evaluations"]
        return {
            **self._stats,
            "pass_rate": (
                round(self._stats["pass_count"] / total, 4) if total > 0 else 0
            ),
            "avg_score": (
                round(sum(h["score"] for h in self._history) / len(self._history), 4)
                if self._history
                else 0
            ),
            "history_count": len(self._history),
        }

    def get_history(self) -> list[dict]:
        """获取评分历史。"""
        return self._history
