# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""★ S45-007: BusinessCapabilitiesPlugin — 业务能力检测插件
====================================================
将现有 BusinessCapabilities 单例包装为 BasePlugin 子类，
通过插件系统统一管理 12 个通用业务能力的检测与注册。

钩子介入点：
  - on_before_step: 步骤执行前检测当前步骤的业务能力，
    将检测结果写入 context 供后续插件/引擎使用。
  - on_before_intent: 测试用例执行前检测整体业务能力，
    将能力 hint 写入 context 供能力缓存使用。

配置（YAML step4_plugins.business_capabilities）：
  enabled: true   # 是否启用
"""

import logging
from typing import Any

from .plugin_manager import BasePlugin

_log = logging.getLogger(__name__)


class BusinessCapabilitiesPlugin(BasePlugin):
    """★ S45-007: 业务能力检测插件。

    包装 BusinessCapabilities 单例，通过插件钩子在步骤/用例执行前
    自动检测业务能力，将结果注入 context 供下游使用。
    """

    name = "business_capabilities"

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        # 延迟引用单例（避免 import 时初始化）
        self._bc = None

    def _get_bc(self):
        """延迟获取 BusinessCapabilities 单例。"""
        if self._bc is None:
            with guard("_get_bc: from src.pipeline.steps.step4.plugin", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step4.plugins.business_capabilities import (
                    business_capabilities,
                )

                self._bc = business_capabilities
        return self._bc

    def on_before_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """★ S45-007: 步骤执行前检测业务能力。

        ctx 包含: step, intent_schema, case_id
        检测结果写入 ctx["business_capability"] 供下游使用。
        """
        bc = self._get_bc()
        if bc is None:
            return None

        step = ctx.get("step") or {}
        # 从 step 中提取目标文本用于能力检测
        goal_text = ""
        if isinstance(step, dict):
            goal_text = (
                step.get("description", "")
                or step.get("action", "")
                or step.get("target_hint", "")
                or step.get("goal", "")
            )
        elif isinstance(step, str):
            goal_text = step

        if not goal_text:
            return None

        with guard("on_before_step: result = bc.detect_capability({}, go", log=_log, level=logging.WARNING):
            # compact_state 暂用空 dict（插件不依赖页面状态）
            result = bc.detect_capability({}, goal_text, None)
            if result:
                hint, score = result
                _log.debug(
                    "[Plugin:%s] 步骤能力检测: hint=%s score=%.2f",
                    self.name,
                    hint,
                    score,
                )
                return {"business_capability": hint, "business_capability_score": score}
        return None

    def on_before_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """★ S45-007: 测试用例执行前检测整体业务能力。

        ctx 包含: intent, case_id, steps
        检测结果写入 ctx["intent_capability"] 供能力缓存使用。
        """
        bc = self._get_bc()
        if bc is None:
            return None

        intent = ctx.get("intent") or {}
        # 从 intent 中提取目标文本
        goal_text = ""
        if isinstance(intent, dict):
            goal_text = (
                intent.get("title", "")
                or intent.get("description", "")
                or intent.get("goal", "")
            )

        if not goal_text:
            return None

        with guard("on_before_intent: result = bc.detect_capability({}, go", log=_log, level=logging.WARNING):
            result = bc.detect_capability({}, goal_text, intent)
            if result:
                hint, score = result
                _log.debug(
                    "[Plugin:%s] 用例能力检测: hint=%s score=%.2f",
                    self.name,
                    hint,
                    score,
                )
                return {"intent_capability": hint, "intent_capability_score": score}
        return None
