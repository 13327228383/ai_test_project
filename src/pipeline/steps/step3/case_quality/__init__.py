# -*- coding: utf-8 -*-
"""测试用例质量检查 & 去重 & 交叉验证模块（包入口）
====================================================
从 case_generator.py 提取的辅助函数：
- Few-Shot 示例加载
- 多模型交叉验证（高风险测试用例）
- 语义去重 & 关键词去重
- 测试用例指标计算

【2026-06-18 重构】原 551 行单文件已按职责拆为 4 个子文件：
  few_shot.py         — Few-Shot 加载与格式化
  cross_validation.py — 多模型交叉验证
  dedup.py            — 去重 + _case_quality_score + _cosine_sim
  metrics.py          — 测试用例指标计算
"""

# ── re-export：保持调用方接口完全不变 ──
from .few_shot import _load_few_shot_examples, _format_few_shot
from .cross_validation import (
    generate_with_cross_validation,
    _intersect_intents_by_title,
)
from .dedup import (
    deduplicate_intents,
    dedup_remodel,
    _dedup_by_keywords,
    _case_quality_score,
    _cosine_sim,
)
from .metrics import compute_case_metrics

# ★ S3-014: 统一编排三层质量保障流程
from .orchestrator import (
    apply_batch_dedup,
    should_run_cross_validation,
)
