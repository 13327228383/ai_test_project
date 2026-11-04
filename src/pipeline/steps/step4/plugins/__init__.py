# -*- coding: utf-8 -*-
"""
Step4 插件系统
================
所有增强功能以独立插件形式存在，通过 PluginManager 统一管理。
插件在 browser_core 执行链路的关键节点被调用，不影响原有代码结构。

已包含 12 个插件：
- GroundTruth: 动作级状态校验
- VisualBaseline: 视觉基线对比
- SelfHealing: 文本策略自愈合
- SelfHealingVision: 视觉特征自愈合
- LongTermMemory: 长程任务记忆
- PerformanceMonitor: 性能指标采集
- HarnessGovernance: Agent 治理框架
- ScorecardOracle: 评分卡判定机制
- TokenBudget: 上下文 Token 预算管控
- FlakyGuard: Flaky Test 检测与修复
- MultiAgent: 多 Agent 协作架构
- BusinessCapabilities: 12 个通用业务能力检测
"""

from .plugin_manager import PluginManager, PluginHook
from .ground_truth import GroundTruthPlugin
from .visual_baseline import VisualBaselinePlugin
from .self_healing import SelfHealingPlugin
from .self_healing_vision import SelfHealingVisionPlugin
from .long_term_memory import LongTermMemoryPlugin
from .performance_monitor import PerformanceMonitorPlugin
from .harness_governance import HarnessGovernancePlugin
from .scorecard_oracle import ScorecardOraclePlugin
from .token_budget import TokenBudgetPlugin
from .flaky_guard import FlakyGuardPlugin
from .multi_agent import MultiAgentPlugin, AgentRole

# ★ S45-007: 业务能力检测插件（包装 BusinessCapabilities 单例）
from .business_capabilities_plugin import BusinessCapabilitiesPlugin

# ★ CDP 客户端浏览器指标收集插件
from .cdp_metrics_plugin import CdpMetricsPlugin

# ★ 性能门禁采集插件（perf_gate 链路数据源）
from .perf_collector_plugin import PerfCollectorPlugin

__all__ = [
    "PluginManager",
    "PluginHook",
    "GroundTruthPlugin",
    "VisualBaselinePlugin",
    "SelfHealingPlugin",
    "SelfHealingVisionPlugin",
    "LongTermMemoryPlugin",
    "PerformanceMonitorPlugin",
    "HarnessGovernancePlugin",
    "ScorecardOraclePlugin",
    "TokenBudgetPlugin",
    "FlakyGuardPlugin",
    "MultiAgentPlugin",
    "AgentRole",
    "BusinessCapabilitiesPlugin",
    "CdpMetricsPlugin",
    "PerfCollectorPlugin",
]
