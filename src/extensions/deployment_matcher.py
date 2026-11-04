# -*- coding: utf-8 -*-
"""
部署文档 → 测试用例匹配 模块（兼容层）
===============================
此文件已拆分为 src/extensions/deployment/ 包。
保留此文件以确保向后兼容 — 所有导入路径不变。

新结构:
- src/extensions/deployment/parser.py  — 数据类 + 文档解析
- src/extensions/deployment/matcher.py — DeploymentMatcher + 匹配逻辑
- src/extensions/deployment/preview.py — 轻量预览
"""

from src.extensions.deployment.parser import (
    DeploymentRow,
    MatchDetail,
    FeatureMatchResult,
    MatchReport,
    COLUMN_ALIASES,
    SECTION_DETECTION_THRESHOLD,
    PHASE1_THRESHOLD,
    PHASE1_THRESHOLD_FALLBACK,
    PHASE2_AUTO,
    PHASE2_AUTO_FALLBACK,
    PHASE2_REVIEW,
)

from src.extensions.deployment.matcher import (
    DeploymentMatcher,
    filter_intents_by_deployment,
    format_report,
)

from src.extensions.deployment.preview import (
    preview_versions,
)
