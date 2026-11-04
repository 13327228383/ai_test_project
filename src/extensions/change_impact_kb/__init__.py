# -*- coding: utf-8 -*-
"""变更影响面 → 回归范围 知识库（零配置可用）。

对外导出：
- ChangeImpactKB：知识库核心（可由外部注入数据，便于单测；生产由 build_from_engine 从 executions 表构建）
- build_change_impact_section / render_change_impact_section：Step5 报告区块（对接 report_generator）

设计约束（对齐"用户使用成本不能高"）：
- 默认开箱即用：知识完全来自引擎已有的 executions 表 + project_configs，
  无需任何新配置、新表、新外部依赖、无 AI 调用。
- 仅在 config.yaml 暴露 3 个可选调优项（均带默认值与中文注释），用户通常无需改动。
- 检索使用轻量关键词/重叠度打分，确定性、零成本；语义增强可后续叠加（复用现有 semantic_index）。
"""
from .kb import ChangeImpactKB
from .report_section import (
    build_change_impact_section,
    render_change_impact_section,
)

__all__ = [
    "ChangeImpactKB",
    "build_change_impact_section",
    "render_change_impact_section",
]
