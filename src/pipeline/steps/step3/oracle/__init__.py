# -*- coding: utf-8 -*-
"""ADI（Assertion Depth Index）门禁 Oracle 包（T01，W2 P0-3）。

导出：
  compute_adi(case)      —— 深度式 ADI 计算内核（adi.py）
  apply_adi_gate(cases)  —— 门禁编排（guard.py）
  ADI_SCORE_TABLE        —— 46 族深度评分表（adi.py）

★ §14 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]：
  本包为 W2 P0-3 ADI fail-closed 门禁的计算内核（≥2 文件新增），
  涉及 Step3 落库核心路径，需强模型（GLM/DeepSeek）复核后再进入灰度。
  未经过强模型复核，请勿视为已审核。
"""
from src.pipeline.steps.step3.oracle.adi import (
    ADI_SCORE_TABLE,
    check_taxonomy_coverage,
    compute_adi,
)
from src.pipeline.steps.step3.oracle.guard import (
    LAST_ADI_GATE_REPORT,
    apply_adi_gate,
)

__all__ = [
    "ADI_SCORE_TABLE",
    "check_taxonomy_coverage",
    "compute_adi",
    "apply_adi_gate",
    "LAST_ADI_GATE_REPORT",
]
