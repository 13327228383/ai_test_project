# -*- coding: utf-8 -*-
"""
pipeline_plugins.py — 全流水线插件 API
=======================================
提供各阶段插件的注册状态查询接口。

★ 修复说明：原实现每次 new 一个空实例查询注册数，永远返回 0。
改为从 bootstrap.list_plugins() 读取应用启动时已初始化的实例。
"""

from flask import Blueprint, jsonify
import logging

_log = logging.getLogger(__name__)

pipeline_plugins_bp = Blueprint("pipeline_plugins", __name__)


@pipeline_plugins_bp.route("/api/pipeline-plugin-metrics")
def api_pipeline_plugin_metrics():
    """★ v5: 返回全流水线插件指标（从已初始化的实例读取）。"""
    try:
        from src.pipeline.plugins.bootstrap import list_plugins

        plugins = list_plugins()

        if not plugins:
            return jsonify(
                {
                    "ok": True,
                    "datasource_count": 0,
                    "strategy_count": 0,
                    "domain_count": 0,
                    "generator_count": 0,
                    "validator_count": 0,
                    "template_count": 0,
                    "section_count": 0,
                    "exporter_count": 0,
                    "distributor_count": 0,
                    "note": "插件系统未初始化",
                }
            )

        # Step1 数据源
        ds = plugins.get("step1_datasource")
        ds_count = len(ds.list_datasources()) if ds else 0

        # Step2 分析策略
        st = plugins.get("step2_analyzer")
        strategy_count = len(st.list_strategies()) if st else 0
        domain_count = len(st.list_domains()) if st else 0

        # Step3 生成策略
        gen = plugins.get("step3_generator")
        generator_count = len(gen.list_generators()) if gen else 0
        validator_count = len(gen.list_validators()) if gen else 0
        template_count = len(gen.list_templates()) if gen else 0

        # Step5 报告扩展
        rep = plugins.get("step5_report")
        section_count = len(rep.list_sections()) if rep else 0
        exporter_count = len(rep.list_exporters()) if rep else 0
        distributor_count = len(rep.list_distributors()) if rep else 0

        return jsonify(
            {
                "ok": True,
                "datasource_count": ds_count,
                "strategy_count": strategy_count,
                "domain_count": domain_count,
                "generator_count": generator_count,
                "validator_count": validator_count,
                "template_count": template_count,
                "section_count": section_count,
                "exporter_count": exporter_count,
                "distributor_count": distributor_count,
            }
        )
    except Exception as e:
        _log.error("[api_pipeline_plugin_metrics] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
