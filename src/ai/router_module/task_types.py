# -*- coding: utf-8 -*-
"""TaskType 枚举 + 模型定价表 + 任务-模型映射 — 从 router_module.py 拆出，零业务逻辑变更。"""

from enum import Enum


class TaskType(str, Enum):
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    TEST_CASE_MODELING = "test_case_modeling"
    TEST_DESIGN = "test_design"  # XMind 测试设计
    OPERATION_INSTRUCTION = "operation_instruction"
    COMPLEX_REASONING = "complex_reasoning"
    VERIFICATION = "verification"  # ★ 校验/自查（测试用例校验、幻觉检测）
    GENERAL = "general"


_MODEL_PRICING = {
    # ── 本地 Ollama ──
    "qwen2.5:7b": {
        "input": 0.0,
        "output": 0.0,
        "tier": "local",
        "description": "本地 Ollama qwen2.5:7b — 免费",
    },
    # ── DeepSeek ──
    "deepseek-chat": {
        "input": 0.14,
        "output": 0.28,
        "tier": "budget",
        "description": "DeepSeek-V3 — 性价比极高",
    },
    "deepseek-reasoner": {
        "input": 0.55,
        "output": 2.19,
        "tier": "premium",
        "description": "DeepSeek-R1 推理模型",
    },
    "deepseek-v4-pro": {
        "input": 0.435,
        "output": 0.87,
        "tier": "flagship",
        "description": "DeepSeek-V4-Pro 旗舰",
    },
    "deepseek-v4-flash": {
        "input": 0.14,
        "output": 0.28,
        "tier": "budget",
        "description": "DeepSeek-V4-Flash 快速版",
    },
    # ── OpenAI ──
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
        "tier": "premium",
        "description": "GPT-4o",
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60,
        "tier": "budget",
        "description": "GPT-4o Mini",
    },
    # ── Anthropic ──
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "tier": "premium",
        "description": "Claude Sonnet 4.6",
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "tier": "budget",
        "description": "Claude Haiku 4.5",
    },
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "tier": "flagship",
        "description": "Claude Opus 4.7",
    },
}

# ★ 零硬编码: _TASK_MODEL_MAP 已删除。模型选择统一由 config.ai_model / config.verification_model 驱动。
# 调用方通过 router.route(cloud_model=xxx) 显式指定，或由 select_cloud_model 从 config 读取。
