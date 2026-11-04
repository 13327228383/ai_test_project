# -*- coding: utf-8 -*-
"""
部署文档 解析模块
================
数据类 + 文档解析 + 表格解析 + 版本过滤 + 静态工具函数。
"""

# re-export 所有公开符号
from ._models import (
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
from ._section_parser import (
    parse_markdown_sections,
    detect_transfer_scope_section,
)
from ._table_parser import (
    parse_table,
    filter_by_version,
    list_versions,
)
from ._utils import (
    extract_keywords_static,
    cosine_sim_static,
)

__all__ = [
    # dataclasses
    "DeploymentRow",
    "MatchDetail",
    "FeatureMatchResult",
    "MatchReport",
    # constants
    "COLUMN_ALIASES",
    "SECTION_DETECTION_THRESHOLD",
    "PHASE1_THRESHOLD",
    "PHASE1_THRESHOLD_FALLBACK",
    "PHASE2_AUTO",
    "PHASE2_AUTO_FALLBACK",
    "PHASE2_REVIEW",
    # section parser
    "parse_markdown_sections",
    "detect_transfer_scope_section",
    # table parser
    "parse_table",
    "filter_by_version",
    "list_versions",
    # utils
    "extract_keywords_static",
    "cosine_sim_static",
]
