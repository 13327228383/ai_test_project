# -*- coding: utf-8 -*-
"""
Step 3: 测试用例生成模块 (CaseGenerator) — v3.1
================================================
每个需求独立 AI 调用（含独立 RAG 检索），ThreadPoolExecutor 并行执行。

核心资产（v3.0）：
- case_id / title / actions / checks[{track, precision, check, expect}]
- risk_level / type
"""

from ._model import model_test_cases

__all__ = [
    "model_test_cases",
]
