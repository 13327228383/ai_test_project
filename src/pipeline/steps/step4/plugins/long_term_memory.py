# -*- coding: utf-8 -*-
from __future__ import annotations

"""
长程任务记忆插件
=================
为长测试用例执行提供分层记忆系统，防止 AI 遗忘早期上下文。

介入点：BEFORE_STEP（注入记忆上下文）、AFTER_STEP（记录步骤结果）
存储：内存 + MySQL（可选）
"""

import logging
from typing import Any
from collections import deque

_log = logging.getLogger(__name__)


class LongTermMemoryPlugin:
    """长程任务记忆插件。"""

    name = "long_term_memory"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 短期记忆容量（最近 N 个步骤）
        self._short_term_capacity = self.config.get("short_term_capacity", 10)
        # 长期记忆容量（关键步骤摘要）
        self._long_term_capacity = self.config.get("long_term_capacity", 20)
        # 记忆存储（按 case_id 隔离）
        self._memories: dict[str, dict[str, Any]] = {}

    def on_before_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """测试用例开始前初始化记忆。"""
        case_id = ctx.get("case_id", "unknown")
        self._memories[case_id] = {
            "short_term": deque(maxlen=self._short_term_capacity),
            "long_term": deque(maxlen=self._long_term_capacity),
            "key_facts": {},  # 关键事实（如"已登录"、"已创建订单#123"）
            "step_count": 0,
        }
        _log.debug("[LongTermMemory] 初始化记忆: %s", case_id)
        return None

    def on_before_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """步骤开始前，注入记忆上下文。"""
        case_id = ctx.get("case_id", "unknown")
        memory = self._memories.get(case_id)
        if memory is None:
            return None

        # 构建记忆摘要
        memory_text = self._build_memory_summary(memory)

        if memory_text:
            _log.debug("[LongTermMemory] 注入记忆上下文 (%d 字符)", len(memory_text))
            return {
                "memory_context": memory_text,
                "memory_injected": True,
            }
        return None

    def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """步骤完成后，记录到记忆。"""
        case_id = ctx.get("case_id", "unknown")
        memory = self._memories.get(case_id)
        if memory is None:
            return None

        step = ctx.get("step", {})
        result = ctx.get("result", False)
        step_desc = step.get("description", "")

        # 记录到短期记忆
        memory["short_term"].append(
            {
                "step_desc": step_desc[:100],
                "result": result,
                "step_index": memory["step_count"],
            }
        )

        # 提取关键事实（简化版：从动作目标描述中提取）
        self._extract_key_facts(step_desc, result, memory["key_facts"])

        memory["step_count"] += 1

        # 每 5 步做一次长期记忆压缩
        if memory["step_count"] % 5 == 0:
            self._compress_to_long_term(memory)

        return None

    def on_after_intent(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """测试用例结束后清理记忆。"""
        case_id = ctx.get("case_id", "unknown")
        if case_id in self._memories:
            del self._memories[case_id]
            _log.debug("[LongTermMemory] 清理记忆: %s", case_id)
        return None

    def _build_memory_summary(self, memory: dict[str, Any]) -> str:
        """构建记忆摘要文本，供 AI prompt 使用。"""
        parts = []

        # 关键事实
        if memory["key_facts"]:
            facts = [f"- {k}: {v}" for k, v in memory["key_facts"].items()]
            parts.append("【已确认的关键事实】\n" + "\n".join(facts))

        # 长期记忆（压缩后的历史）
        if memory["long_term"]:
            long_items = [f"- {m['summary']}" for m in memory["long_term"]]
            parts.append("【历史摘要】\n" + "\n".join(long_items))

        # 短期记忆（最近动作）
        if memory["short_term"]:
            short_items = []
            for m in memory["short_term"]:
                status = "✓" if m["result"] else "✗"
                short_items.append(f"  {status} {m['step_desc']}")
            parts.append("【最近操作】\n" + "\n".join(short_items))

        return "\n\n".join(parts)

    def _extract_key_facts(
        self, step_desc: str, result: bool, key_facts: dict[str, str]
    ):
        """从动作目标描述中提取关键事实。"""
        if not result:
            return  # 失败的动作不提取事实

        desc = step_desc.lower()

        # 登录状态
        if any(kw in desc for kw in ["登录", "login", "登入"]):
            key_facts["登录状态"] = "已登录"

        # 创建记录
        if any(kw in desc for kw in ["创建", "新增", "create", "add"]):
            key_facts["最近操作"] = "已创建记录"

        # 删除记录
        if any(kw in desc for kw in ["删除", "移除", "delete", "remove"]):
            key_facts["最近操作"] = "已删除记录"

        # 页面位置
        if any(kw in desc for kw in ["首页", "home", "主页"]):
            key_facts["当前页面"] = "首页"
        elif any(kw in desc for kw in ["列表", "list", "table"]):
            key_facts["当前页面"] = "列表页"
        elif any(kw in desc for kw in ["详情", "detail"]):
            key_facts["当前页面"] = "详情页"

    def _compress_to_long_term(self, memory: dict[str, Any]):
        """将短期记忆压缩为长期记忆摘要。"""
        short_term = list(memory["short_term"])
        if not short_term:
            return

        # 简单压缩：统计成功/失败数量 + 关键操作类型
        success_count = sum(1 for m in short_term if m["result"])
        fail_count = len(short_term) - success_count

        summary = f"步骤 {memory['step_count'] - len(short_term) + 1}~{memory['step_count']}: "
        summary += f"成功{success_count}次"
        if fail_count > 0:
            summary += f", 失败{fail_count}次"

        # 提取关键操作
        key_ops = []
        for m in short_term:
            desc = m["step_desc"].lower()
            if any(kw in desc for kw in ["创建", "新增", "create"]):
                key_ops.append("创建")
            elif any(kw in desc for kw in ["删除", "remove", "delete"]):
                key_ops.append("删除")
            elif any(kw in desc for kw in ["搜索", "查询", "search"]):
                key_ops.append("搜索")

        if key_ops:
            summary += f", 关键操作: {', '.join(set(key_ops))}"

        memory["long_term"].append({"summary": summary})
        _log.debug("[LongTermMemory] 压缩长期记忆: %s", summary)
