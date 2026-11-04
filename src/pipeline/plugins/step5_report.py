# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
Step5 报告章节插件
==================
支持动态注册报告章节和导出格式。

介入点：STEP5_START（注入章节）
"""

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class Step5ReportPlugin:
    """Step5 报告章节插件。"""

    name = "step5_report"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 报告章节注册表
        self._sections: dict[str, dict] = {}
        # 导出格式注册表
        self._exporters: dict[str, dict] = {}
        # 分发渠道注册表
        self._distributors: dict[str, dict] = {}

    def register_section(
        self,
        name: str,
        render_func: Callable,
        position: str = "after_overview",
        priority: int = 50,
    ):
        """
        注册报告章节。

        参数：
            name: 章节标识（如 "security_audit", "performance_summary"）
            render_func: 渲染函数，签名 render_func(context) → markdown_text
            position: 插入位置（before_overview/after_overview/after_details/appendix）
            priority: 优先级（越小越靠前）
        """
        self._sections[name] = {
            "render": render_func,
            "position": position,
            "priority": priority,
        }
        _log.info(
            "[Step5Report] 注册章节: %s (position=%s, priority=%d)",
            name,
            position,
            priority,
        )

    def register_exporter(
        self, name: str, export_func: Callable, format_name: str, mime_type: str
    ):
        """
        注册导出格式。

        参数：
            name: 导出器标识（如 "pdf", "jira_csv"）
            export_func: 导出函数，签名 export_func(report_data, output_path) → success_bool
            format_name: 格式名称（供前端展示）
            mime_type: MIME 类型
        """
        self._exporters[name] = {
            "export": export_func,
            "format_name": format_name,
            "mime_type": mime_type,
        }
        _log.info("[Step5Report] 注册导出器: %s (%s)", name, format_name)

    def register_distributor(
        self,
        name: str,
        distribute_func: Callable,
        channel_type: str,
        config_schema: dict = None,
    ):
        """
        注册报告分发渠道。

        参数：
            name: 渠道标识（如 "wechat", "dingtalk", "email", "testrail"）
            distribute_func: 分发函数，签名 distribute_func(report_data, config) → result
            channel_type: 渠道类型（im/email/platform）
            config_schema: 配置项 Schema（供前端展示表单）
        """
        self._distributors[name] = {
            "distribute": distribute_func,
            "channel_type": channel_type,
            "schema": config_schema or {},
        }
        _log.info("[Step5Report] 注册分发渠道: %s (%s)", name, channel_type)

    def on_step5_start(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step5 开始时，注入报告章节、导出器和分发渠道。

        ctx 包含:
            project: str
            sections: dict — 当前章节列表
            exporters: dict — 当前导出器列表
            distributors: dict — 当前分发渠道列表
        """
        if not self.enabled:
            return None

        result = {}

        # 注入章节
        sections = ctx.get("sections", {})
        for name, sec in self._sections.items():
            if name not in sections:
                sections[name] = sec
        if sections:
            result["sections"] = sections

        # 注入导出器
        exporters = ctx.get("exporters", {})
        for name, exp in self._exporters.items():
            if name not in exporters:
                exporters[name] = exp
        if exporters:
            result["exporters"] = exporters

        # 注入分发渠道
        distributors = ctx.get("distributors", {})
        for name, dist in self._distributors.items():
            if name not in distributors:
                distributors[name] = dist
        if distributors:
            result["distributors"] = distributors

        if result:
            _log.info(
                "[Step5Report] 注入 %d 个章节, %d 个导出器, %d 个分发渠道",
                len(sections),
                len(exporters),
                len(distributors),
            )
        return result if result else None

    def on_step5_complete(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step5 完成后，执行自动分发。

        ctx 包含:
            project: str
            report_data: dict — 报告数据
            auto_distribute: list — 自动分发渠道列表
        """
        if not self.enabled:
            return None

        auto_channels = ctx.get("auto_distribute", [])
        if not auto_channels:
            return None

        report_data = ctx.get("report_data", {})
        results = {}

        for channel in auto_channels:
            dist = self._distributors.get(channel)
            if dist:
                try:
                    result = dist["distribute"](report_data, dist.get("config", {}))
                    results[channel] = {"success": True, "result": result}
                except Exception as e:
                    results[channel] = {"success": False, "error": str(e)[:120]}
            else:
                results[channel] = {"success": False, "error": "未注册的分发渠道"}

        _log.info("[Step5Report] 自动分发结果: %s", results)
        return {"distribute_results": results}

    def on_pipeline_complete(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        整条流水线完成后，生成汇总报告。

        ctx 包含:
            project: str
            execution_id: str
            step_results: dict — 各阶段结果
        """
        if not self.enabled:
            return None

        step_results = ctx.get("step_results", {})
        summary = {
            "project": ctx.get("project"),
            "execution_id": ctx.get("execution_id"),
            "steps_completed": list(step_results.keys()),
            "total_cases": step_results.get("step3", {}).get("intent_count", 0),
            "passed_intents": step_results.get("step4", {}).get("passed_count", 0),
            "failed_intents": step_results.get("step4", {}).get("failed_count", 0),
            "report_generated": "step5" in step_results,
        }

        # ★ 快照运行期插件注册数（供「流水线扩展」面板读到真实值，而非读端进程启动空壳）
        with guard("on_pipeline_complete: project = ctx.get('project')", log=_log):
            project = ctx.get("project")
            rid = ""
            try:
                from src.backend.state import _thread_run_id

                rid = getattr(_thread_run_id, "value", "") or ""
            except Exception:
                rid = ""
            rid = rid or ctx.get("execution_id") or ""
            if project and rid:
                from src.pipeline.plugins.bootstrap import snapshot_registration_counts

                snapshot_registration_counts(project, rid)

        _log.info("[Step5Report] 流水线汇总: %s", summary)
        return {"pipeline_summary": summary}

    def list_sections(self) -> dict[str, dict]:
        """列出所有已注册的章节。"""
        return {
            name: {"position": s["position"], "priority": s["priority"]}
            for name, s in self._sections.items()
        }

    def list_exporters(self) -> dict[str, dict]:
        """列出所有已注册的导出器。"""
        return {
            name: {"format_name": e["format_name"], "mime_type": e["mime_type"]}
            for name, e in self._exporters.items()
        }

    def list_distributors(self) -> dict[str, dict]:
        """列出所有已注册的分发渠道。"""
        return {
            name: {"channel_type": d["channel_type"]}
            for name, d in self._distributors.items()
        }
