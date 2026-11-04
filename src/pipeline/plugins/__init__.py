# -*- coding: utf-8 -*-
"""
pipeline/plugins/ — 全流水线插件系统
=====================================
跨阶段的插件注册与事件总线。
各阶段通过事件总线通信，插件可订阅任意阶段的事件。

★ B-5-2: BasePlugin 和 PluginManager 从 Step4 提升到全流水线级别，
   任意 Step 均可通过 from src.pipeline.plugins import BasePlugin 使用。
"""

from .pipeline_bus import PipelineBus, PipelineEvent
from .step1_datasource import Step1DatasourcePlugin
from .step2_analyzer import Step2AnalyzerPlugin
from .step3_generator import Step3GeneratorPlugin
from .step5_report import Step5ReportPlugin

# ★ B-5-2: 全流水线级 re-export
from src.pipeline.steps.step4.plugins.plugin_manager import (
    BasePlugin,
    PluginHook,
    PluginManager,
    plugin_manager,
)

__all__ = [
    "PipelineBus",
    "PipelineEvent",
    "Step1DatasourcePlugin",
    "Step2AnalyzerPlugin",
    "Step3GeneratorPlugin",
    "Step5ReportPlugin",
    # ★ B-5-2
    "BasePlugin",
    "PluginHook",
    "PluginManager",
    "plugin_manager",
]
