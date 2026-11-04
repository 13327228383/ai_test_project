# -*- coding: utf-8 -*-
"""
追溯链路引擎 (TraceabilityEngine) — v4.0
======================================
构建结构化的 REQ → API → DB → Page 追溯链路，
在 Step2（需求拆解）完成后、Step3（测试用例生成）之前运行。

核心能力：
1. AI 驱动的追溯链路构建：对每个需求分析关联的 API/DB/Page
2. 数据源覆盖率计算：每个 API endpoint、DB 表、Figma 页面是否被覆盖
3. 链路完整性检测：发现断链（有 API 没测试用例、有 DB 表没验证等）
4. 持久化：MySQL trace_links 表；trace_links.json 仅作显式导入/导出附件

设计原则：
- 可独立运行，不依赖其他模块的状态
- AI 失败时回退到关键词启发式匹配
- 输出同时服务于 CoverageTracker 增强和 RAG 索引

.. note::
    本文件已按功能域拆分为 ``traceability/`` 子模块，此处仅保留向后兼容的
    re-export。所有业务逻辑零变更，请直接引用子模块获取更细粒度的依赖控制。
"""

from .traceability.models import TraceLink, CoverageReport
from .traceability.engine import TraceabilityEngine, traceability_engine

__all__ = [
    "TraceLink",
    "CoverageReport",
    "TraceabilityEngine",
    "traceability_engine",
]
