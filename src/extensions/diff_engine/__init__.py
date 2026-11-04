# -*- coding: utf-8 -*-
"""
变更对比引擎 (DiffEngine) + 影响分析器 (ImpactAnalyzer) — v4.0（包入口）
========================================================================
在每次 Step1 拉取新数据后，与上一版本的快照做语义对比，
识别变更点并分析受影响的需求和测试用例。

核心能力：
1. 需求文档 diff：对比新旧需求，识别新增/修改/删除的需求条目
2. API schema diff：对比 YApi 接口文档，识别接口/字段变更
3. DB schema diff：对比数据库 schema，识别表/列变更
4. 快照管理：每次运行保存版本快照到 versions/ 目录
5. 影响分析：基于 TraceLink 交叉分析变更影响的需求和测试用例

设计原则：
- 有快照就做 diff，没快照就只保存快照（首次运行不报错）
- AI 做语义 diff（需求级别），规则引擎做结构 diff（API/DB 级别）
- diff 结果驱动选择性重新生成，避免全量重来

【2026-06-18 重构】原 661 行单文件已按职责拆为：
  models.py  — DiffEntry / DiffReport / ImpactResult 三个 dataclass
  engine.py  — DiffEngine 主类（快照、需求/API/DB diff、报告格式化）
  impact.py  — ImpactAnalyzer（基于 TraceLink 的影响分析）
  __init__.py — 单例 diff_engine / impact_analyzer + re-export
"""


# ── re-export：保持调用方接口完全不变 ──
from .models import DiffEntry, DiffReport, ImpactResult
from .engine import DiffEngine
from .impact import ImpactAnalyzer


# 单例（全走 MySQL diff_snapshots，无需 project_root）
diff_engine = DiffEngine()
impact_analyzer = ImpactAnalyzer()
