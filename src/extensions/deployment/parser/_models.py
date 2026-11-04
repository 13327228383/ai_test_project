# -*- coding: utf-8 -*-
"""
parser._models — 数据结构 + 常量
================================
DeploymentRow, MatchDetail, FeatureMatchResult, MatchReport 及所有常量。
"""

from dataclasses import dataclass, field

# ============================================================
# 数据结构
# ============================================================


@dataclass
class DeploymentRow:
    """部署文档中的一行转测功能点。"""

    module: str  # 模块名，如 "数据看板"
    feature: str  # 功能点，如 "醇化分布图"
    version: str  # 转测版本，如 "V2.5.6"
    prd_priority: str  # PRD 优先级
    notes: str  # 备注/代办


@dataclass
class MatchDetail:
    """单个测试用例的匹配详情。"""

    case_id: str
    title: str
    phase1_score: float  # 模块名相似度 (0-1)
    phase2_score: float  # 功能点语义相似度 (0-1)
    combined_score: float  # 加权综合分
    method: str  # "embedding" / "keyword"


@dataclass
class FeatureMatchResult:
    """一个功能点的匹配结果。"""

    deployment_row: DeploymentRow
    matched_intents: list[MatchDetail] = field(default_factory=list)
    low_confidence: list[MatchDetail] = field(default_factory=list)
    unmatched: bool = True


@dataclass
class MatchReport:
    """匹配报告。"""

    total_features: int = 0
    matched_features: int = 0
    unmatched_features: list[DeploymentRow] = field(default_factory=list)
    low_confidence_matches: list = field(default_factory=list)
    results: list[FeatureMatchResult] = field(default_factory=list)


# ============================================================
# 常量
# ============================================================

# 列名别名映射 —— 不依赖列顺序，按表头文字识别
COLUMN_ALIASES: dict[str, list[str]] = {
    "module": ["模块", "模块名", "功能模块", "module"],
    "feature": ["功能点", "功能", "功能描述", "feature"],
    "version": ["转测版本", "版本", "版本号", "转测版本号", "version"],
    "prd_priority": ["prd优先级", "优先级", "prd优先級", "priority"],
    "notes": ["深算院代办", "代办", "备注", "说明", "notes"],
}

# 章节检测评分
SECTION_DETECTION_THRESHOLD = 50  # 最低分数才判定为转测范围章节

# 匹配阈值
PHASE1_THRESHOLD = 0.3  # Phase1 模块名最低相似度（embedding 可用时）
PHASE1_THRESHOLD_FALLBACK = 0.02  # 无 embedding 时更宽松，让 Phase2 做主力
PHASE2_AUTO = 0.6  # Phase2 自动匹配置信度（embedding 时）
PHASE2_AUTO_FALLBACK = 0.10  # 无 embedding 时 SequenceMatcher 自动匹配置信度
PHASE2_REVIEW = 0.06  # Phase2 需人工审核的下限
