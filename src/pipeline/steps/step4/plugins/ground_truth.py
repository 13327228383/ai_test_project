# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
Ground Truth 校验插件
=====================
每个动作执行后，验证页面真实状态是否按预期变化。
防止 AI 幻觉"我点击了"但实际上页面没变。

介入点：AFTER_ACTION
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class GroundTruthPlugin:
    """动作级 Ground Truth 校验插件。"""

    name = "ground_truth"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 配置：哪些动作类型需要强制校验
        self._verify_actions = set(
            self.config.get(
                "verify_actions",
                [
                    "click",
                    "fill",
                    "select_option",
                    "check",
                    "uncheck",
                    "double_click",
                    "right_click",
                    "drag",
                ],
            )
        )
        # 配置：状态变化的容忍阈值
        self._min_text_delta = self.config.get("min_text_delta", 0)
        self._min_elem_delta = self.config.get("min_elem_delta", 0)

    def on_after_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行后校验页面状态是否变化。

        ctx 包含:
            action: dict — 动作定义
            result: bool — 动作执行结果
            before_state: dict — 执行前页面状态
            after_state: dict — 执行后页面状态
            engine: PlaywrightEngine — 浏览器引擎
        """
        action = ctx.get("action", {})
        action_type = action.get("action", "").lower()

        # 只校验指定类型的动作
        if action_type not in self._verify_actions:
            return None

        # 动作本身执行失败，不需要 Ground Truth 校验（已有错误）
        if not ctx.get("result", True):
            return None

        before = ctx.get("before_state", {})
        after = ctx.get("after_state", {})

        # 状态未变化 → 可能点击失败或点了无效元素
        if not self._state_changed(before, after):
            _log.warning(
                "[GroundTruth] ⚠ %s 后页面状态未变化，可能操作无效 "
                "(url=%s → %s, title=%s → %s, text_len=%s → %s, elem=%s → %s)",
                action_type,
                before.get("url", "?"),
                after.get("url", "?"),
                before.get("title", "?")[:30],
                after.get("title", "?")[:30],
                before.get("text_len", 0),
                after.get("text_len", 0),
                before.get("element_count", 0),
                after.get("element_count", 0),
            )
            self._record_gt_metric(gt_passed=False)
            return {
                "ground_truth_passed": False,
                "ground_truth_reason": f"{action_type} 后页面状态未变化，可能操作无效",
            }

        _log.debug("[GroundTruth] ✓ %s 后页面状态已变化", action_type)
        self._record_gt_metric(gt_passed=True)
        return {"ground_truth_passed": True}

    def _record_gt_metric(self, gt_passed: bool):
        """记录 Ground Truth 校验结果到 ai_health_metrics。"""
        with guard("_record_gt_metric: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if run_id:
                metrics_collector.record_ground_truth(
                    project=self._get_project(),
                    run_id=run_id,
                    total_checks=1,
                    mismatches_found=0 if gt_passed else 1,
                    mismatches_by_field={},
                )

    def _get_project(self) -> str:
        """获取当前项目名。"""
        try:
            from src.core.config_loader import config

            return config.project_name
        except Exception as e:
            _log.warning("GroundTruth项目名获取异常: %s", e, exc_info=True)
            return ""

    def _state_changed(self, before: dict[str, Any], after: dict[str, Any]) -> bool:
        """判断页面状态是否发生有意义的变化。"""
        if not before or not after:
            return False  # 无法获取状态，无法确认变化（禁止保守通过）

        # URL 变化 → 肯定变了
        if before.get("url") != after.get("url"):
            return True

        # 标题变化
        if before.get("title") != after.get("title"):
            return True

        # 文本长度变化超过阈值
        text_delta = abs(
            int(after.get("text_len", 0) or 0) - int(before.get("text_len", 0) or 0)
        )
        if text_delta > self._min_text_delta:
            return True

        # 元素数量变化超过阈值
        elem_delta = abs(
            int(after.get("element_count", 0) or 0)
            - int(before.get("element_count", 0) or 0)
        )
        if elem_delta > self._min_elem_delta:
            return True

        # 文本头部内容变化（前200字符）
        if before.get("text_head") != after.get("text_head"):
            return True

        return False
