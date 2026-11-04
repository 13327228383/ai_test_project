# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
插件系统启动引导
================
在应用启动时实例化 4 个流水线插件，并 subscribe 到 pipeline_bus。
注册内置的数据源/分析器策略，使 dom_crawler 和 feature_driven 可被插件系统观察。

调用入口：
    from src.pipeline.plugins.bootstrap import init_pipeline_plugins
    init_pipeline_plugins()
"""

import functools
import logging

_log = logging.getLogger(__name__)

_initialized: bool = False
_registered_plugins: dict = {}


def init_pipeline_plugins(config: dict | None = None) -> dict:
    """
    初始化全流水线插件系统。

    参数:
        config: 可选配置字典（预留，目前未使用）

    返回:
        dict: {"plugins": {name: instance}, "subscribed": int}
    """
    global _initialized, _registered_plugins
    if _initialized:
        _log.debug("[Plugins] 已初始化，跳过重复调用")
        return {"plugins": _registered_plugins, "subscribed": 0}

    from .pipeline_bus import pipeline_bus, PipelineEvent
    from .step1_datasource import Step1DatasourcePlugin
    from .step2_analyzer import Step2AnalyzerPlugin
    from .step3_generator import Step3GeneratorPlugin
    from .step5_report import Step5ReportPlugin

    plugins = {
        "step1_datasource": Step1DatasourcePlugin(config or {}),
        "step2_analyzer": Step2AnalyzerPlugin(config or {}),
        "step3_generator": Step3GeneratorPlugin(config or {}),
        "step5_report": Step5ReportPlugin(config or {}),
    }

    # 订阅事件 → 回调绑定
    pipeline_bus.subscribe(
        PipelineEvent.STEP1_START, _wrap(plugins["step1_datasource"].on_step1_start)
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP1_COMPLETE,
        _wrap(plugins["step1_datasource"].on_step1_complete),
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP2_START, _wrap(plugins["step2_analyzer"].on_step2_start)
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP2_COMPLETE, _wrap(plugins["step2_analyzer"].on_step2_complete)
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP3_START, _wrap(plugins["step3_generator"].on_step3_start)
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP3_COMPLETE,
        _wrap(plugins["step3_generator"].on_step3_complete),
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP5_START, _wrap(plugins["step5_report"].on_step5_start)
    )
    pipeline_bus.subscribe(
        PipelineEvent.STEP5_COMPLETE, _wrap(plugins["step5_report"].on_step5_complete)
    )
    pipeline_bus.subscribe(
        PipelineEvent.PIPELINE_COMPLETE,
        _wrap(plugins["step5_report"].on_pipeline_complete),
    )

    _registered_plugins = plugins
    _initialized = True

    _log.info("[Plugins] 已初始化 %d 个插件，订阅 9 个事件", len(plugins))

    # ★ 注册内置策略：feature_driven 分析器
    _register_builtin_strategies(plugins)

    return {"plugins": plugins, "subscribed": 9}


def _wrap(handler):
    """适配器：pipeline_bus.publish 传入 (event, ctx)，插件方法只接受 ctx。"""

    @functools.wraps(handler)
    def _adapter(event, ctx):
        return handler(ctx)

    return _adapter


def _register_builtin_strategies(plugins: dict) -> None:
    """注册内置的数据源和分析器策略（避免硬编码业务规则，只注册通用接入点）。"""
    # ★ P0-2废弃(2026-07-29): dom_crawler 策略已移除，AgentExplorer 实时探索替代

    # ★ 注册 feature_driven 为 Step2 分析器策略（声明性注册，便于插件系统查询）
    with guard("_register_builtin_strategies: step2_plugin = plugins.get('step2_an", log=_log):
        step2_plugin = plugins.get("step2_analyzer")
        if step2_plugin and "feature_driven" not in step2_plugin._strategies:
            step2_plugin.register_strategy(
                name="feature_driven",
                analyze_func=_feature_driven_strategy_stub,
                priority=10,  # 高优先级（功能清单驱动是主路径）
            )


def _feature_driven_strategy_stub(requirements: list, context: dict) -> dict:
    """feature_driven 分析器策略 stub。

    实际拆解逻辑由 src/pipeline/steps/step2/analyzers/feature_driven.py 驱动，
    此 stub 仅用于插件系统元数据完整性。
    """
    _log.debug(
        "[Plugins] feature_driven stub 调用（实际逻辑由 analyzers/feature_driven.py 驱动）"
    )
    return {"issues": [], "note": "stub: 实际逻辑由 analyzers/feature_driven.py 驱动"}


def get_plugin(name: str):
    """获取已初始化的插件实例。"""
    return _registered_plugins.get(name)


def list_plugins() -> dict:
    """列出所有已初始化的插件。"""
    return dict(_registered_plugins)


def _count_registered() -> dict[str, int]:
    """统计当前已注册的 Step1/2/3/5 插件数（datasource/analyzer/generator/report）。"""
    p = _registered_plugins
    out: dict[str, int] = {}
    if "step1_datasource" in p:
        out["step1_datasource"] = len(p["step1_datasource"].list_datasources())
    if "step2_analyzer" in p:
        out["step2_analyzer"] = len(p["step2_analyzer"].list_strategies())
    if "step3_generator" in p:
        g = p["step3_generator"]
        out["step3_generator"] = (
            len(g.list_generators()) + len(g.list_validators()) + len(g.list_templates())
        )
    if "step5_report" in p:
        r = p["step5_report"]
        out["step5_report"] = (
            len(r.list_sections()) + len(r.list_exporters()) + len(r.list_distributors())
        )
    return out


def snapshot_registration_counts(project: str, run_id: str) -> dict[str, int]:
    """将本次运行进程内的插件注册数持久化为 ai_health_metrics 快照。

    ★ 根因修复(2026-09-20)：「流水线扩展」面板原来读常驻指标后端进程的启动注册态，
    而 step1/3/5 策略/生成器/区块只在真实运行进程内懒注册 → 读端永远看到 step1/3/5=0
    （跨进程时序断链）。这里在 PIPELINE_COMPLETE 把运行期注册数按 (project, run_id) 落库，
    读端改读该快照，得到运行期真实值而非启动空壳。
    """
    counts = _count_registered()
    if not counts:
        return counts
    with guard("snapshot_registration_counts: from src.extensions.metrics_collecto", log=_log, level=logging.WARNING):
        from src.extensions.metrics_collector import metrics_collector

        for step_name, cnt in counts.items():
            metrics_collector.record(
                project=project,
                run_id=run_id,
                step=5,
                metric_type="pipeline_ext",
                metric_name=f"{step_name}_count",
                category="count",
                value=cnt,
            )
        _log.info("[Plugins] 已快照插件注册数: %s", counts)
    return counts
