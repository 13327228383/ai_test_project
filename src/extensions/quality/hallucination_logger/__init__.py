# -*- coding: utf-8 -*-
"""
★ P2-2: 幻觉案例记录与分析器 (HallucinationLogger)
====================================================
执行失败的测试用例中，自动分类失败原因——"环境问题" vs "测试用例本身幻觉"。
幻觉案例沉淀到知识库，触发规则/Prompt 更新。

数据流：
  执行失败 → classify_failure() → hallucination? → save_sample()
                                → env? → 忽略
                                → unknown? → 人工标注
  积累 ≥10 样本 → analyze_and_update_rules() → 自动更新 test_rules.json

本模块为 facade，实际实现拆分至：
  - ``_logger``: HallucinationLogger 类、单例、_summarize_reasons、_FAILURE_KEYWORDS
  - ``_feedback``: 闭环反馈函数（历史失败率查询 & Prompt 注入）
"""

from ._logger import (
    HallucinationLogger,
    hallucination_logger,
    _summarize_reasons,
    _FAILURE_KEYWORDS,
)
from ._feedback import (
    get_requirement_failure_history,
    generate_failure_feedback_prompt,
)

__all__ = [
    "HallucinationLogger",
    "hallucination_logger",
    "get_requirement_failure_history",
    "generate_failure_feedback_prompt",
    "_summarize_reasons",
    "_FAILURE_KEYWORDS",
]
