# -*- coding: utf-8 -*-
"""
证据引擎 (Evidence Engine) — 通用置信度校准框架
================================================
核心思想:
  - AI 不做裁判，只做证据收集器
  - 置信度 = 独立证据验证 + 历史实证校准 + 贝叶斯后验
  - 零硬编码、通用系统、MySQL 唯一事实源

模块:
  - calibration.py: 校准数据读写 + 贝叶斯公式
  - confidence.py: 证据驱动置信度计算引擎
"""

from src.pipeline.evidence.calibration import (  # noqa: F401
    record_calibration,
    get_calibration,
    _bayesian_posterior,
)

# ── 延迟导入（避免循环依赖）──
# claim_extractor, independent_verifiers, confidence 由调用方按需导入
