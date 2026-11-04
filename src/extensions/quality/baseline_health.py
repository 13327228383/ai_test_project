# -*- coding: utf-8 -*-
"""
基准健康度检查器 (BaselineHealthChecker)
==========================================
★ P1: 检查Flash校验和交叉校验的基准数据是否就绪。
防止"无基准乱校验"和"静默跳过漏测"。

核心原则：
1. 基准缺失时必须明确告警，不能静默跳过
2. 根据基准完整度决定校验策略（严格/宽松/跳过）
3. 提供一键补齐基准的指引
"""

import logging
from src.core.guarded import guard
from src.core.pipeline_config import pipeline_config
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class BaselineStatus:
    """基准数据状态。"""

    name: str
    exists: bool
    count: int = 0
    required: bool = True  # 是否必需
    source: str = ""  # 数据来源
    how_to_fix: str = ""  # 如何补齐


@dataclass
class HealthReport:
    """健康度报告。"""

    overall: str = "unknown"  # healthy / degraded / critical
    baselines: list[BaselineStatus] = field(default_factory=list)
    can_run_flash: bool = False
    can_run_cross_check: bool = False
    can_run_trace_check: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BaselineHealthChecker:
    """
    基准健康度检查器。

    检查项：
    1. YApi API路径基准（交叉校验API用）
    2. Figma UI元素基准（交叉校验UI用）
    3. DB枚举值基准（交叉校验枚举用）
    4. 追溯链路基准（trace_links，校验DB表/字段用）
    5. DB Schema基准（db_schema，校验字段存在性用）
    """

    def check(self) -> HealthReport:
        """执行完整健康度检查。"""
        report = HealthReport()
        report.baselines = []

        # 1. YApi API路径
        yapi_status = self._check_yapi()
        report.baselines.append(yapi_status)

        # 2. Figma UI元素
        figma_status = self._check_figma()
        report.baselines.append(figma_status)

        # 3. DB枚举值
        enum_status = self._check_db_enums()
        report.baselines.append(enum_status)

        # 4. 追溯链路
        trace_status = self._check_trace_links()
        report.baselines.append(trace_status)

        # 5. DB Schema
        schema_status = self._check_db_schema()
        report.baselines.append(schema_status)

        # 评估整体健康度
        required_exists = sum(1 for b in report.baselines if b.exists and b.required)
        required_total = sum(1 for b in report.baselines if b.required)
        all_exists = sum(1 for b in report.baselines if b.exists)

        if required_exists == required_total and all_exists == len(report.baselines):
            report.overall = "healthy"
        elif (
            required_exists
            >= required_total * pipeline_config.baseline_health_degraded_threshold
        ):
            report.overall = "degraded"
        else:
            report.overall = "critical"

        # 决定能否运行各类校验
        report.can_run_flash = True  # Flash校验不依赖这些基准（靠AI常识）
        report.can_run_cross_check = (
            yapi_status.exists or figma_status.exists or enum_status.exists
        )
        report.can_run_trace_check = trace_status.exists and schema_status.exists

        # 生成告警
        for b in report.baselines:
            if not b.exists and b.required:
                report.errors.append(f"【{b.name}】基准缺失: {b.how_to_fix}")
            elif not b.exists and not b.required:
                report.warnings.append(f"【{b.name}】基准缺失（可选）: {b.how_to_fix}")

        return report

    # ── 各基准检查 ──

    def _check_yapi(self) -> BaselineStatus:
        """检查YApi API路径基准。"""
        status = BaselineStatus(
            name="YApi API路径",
            source="diff_snapshots.api_schema",
            how_to_fix="Step1需配置YApi数据源并执行，或手动导入raw_yapi_docs.json",
        )
        with guard("_check_yapi: from src.pipeline.utils.diff_snapsho", log=_log, level=logging.ERROR):
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            yapi_raw = diff_snapshot_store.load_snapshot("api_schema")
            if yapi_raw:
                items = (
                    yapi_raw.get("data", yapi_raw)
                    if isinstance(yapi_raw, dict)
                    else yapi_raw
                )
                if isinstance(items, dict):
                    items = items.get("list", items.get("apis", []))
                if isinstance(items, list):
                    status.count = len(items)
                    status.exists = status.count > 0
        return status

    def _check_figma(self) -> BaselineStatus:
        """检查Figma UI元素基准（★ 2026-06-30: 从 figma_pages 表替代 nav_map）。"""
        status = BaselineStatus(
            name="Figma UI元素",
            source="figma_pages",
            how_to_fix="Step1需配置Figma数据源并执行",
            required=False,  # 可选
        )
        with guard("_check_figma: from src.core.config_loader import c", log=_log, level=logging.ERROR):
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
            version_label = str(getattr(config, "project_version", "") or "")
            if project and version_label:
                from src.core.db_manager import get_connection

                conn = get_connection()
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM figma_pages WHERE project=%s AND version_label=%s",
                        (project, version_label),
                    ).fetchone()
                    count = int(row["cnt"]) if row else 0
                    # 再加 figma_nodes 计数
                    node_row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM figma_nodes WHERE project=%s AND version_label=%s",
                        (project, version_label),
                    ).fetchone()
                    count += int(node_row["cnt"]) if node_row else 0
                    status.count = count
                    status.exists = count > 0
                finally:
                    with guard("_check_figma: conn.close()", log=_log):
                        conn.close()
        return status

    def _check_db_enums(self) -> BaselineStatus:
        """检查DB枚举值基准。"""
        status = BaselineStatus(
            name="DB枚举值",
            source="diff_snapshots.db_schema",
            how_to_fix="Step1需配置DB数据源并执行，或手动导入system_data_catalog.json",
        )
        with guard("_check_db_enums: from src.pipeline.utils.diff_snapsho", log=_log, level=logging.ERROR):
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            db_snap = diff_snapshot_store.load_snapshot("db_schema")
            if isinstance(db_snap, dict) and "classification" in db_snap:
                count = 0
                for tinfo in db_snap["classification"].values():
                    cols = tinfo.get("columns", {})
                    for col_info in cols.values():
                        if col_info.get("enum_values") or col_info.get("sample_values"):
                            count += 1
                status.count = count
                status.exists = count > 0
        return status

    def _check_trace_links(self) -> BaselineStatus:
        """检查追溯链路基准。"""
        status = BaselineStatus(
            name="追溯链路",
            source="traceability_model.trace_links",
            how_to_fix="Step2需求拆解时会自动构建，确保Step2正常执行",
        )
        try:
            from src.extensions.traceability_model import traceability_model

            if hasattr(traceability_model, "get_trace_links"):
                links = traceability_model.get_trace_links()
                status.count = len(links)
                status.exists = len(links) > 0
        except Exception as e:
            _log.warning("[异常] %s", e, exc_info=True)
        return status

    def _check_db_schema(self) -> BaselineStatus:
        """检查DB Schema基准。"""
        status = BaselineStatus(
            name="DB Schema",
            source="diff_snapshots.db_schema",
            how_to_fix="Step1需配置DB数据源并执行",
        )
        with guard("_check_db_schema: from src.pipeline.utils.diff_snapsho", log=_log, level=logging.ERROR):
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            db_snap = diff_snapshot_store.load_snapshot("db_schema")
            if isinstance(db_snap, dict):
                if "classification" in db_snap:
                    status.count = len(db_snap["classification"])
                    status.exists = status.count > 0
                elif "schema_text" in db_snap:
                    status.count = 1
                    status.exists = True
        return status


# ══════════════════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════════════════


def check_baseline_health() -> HealthReport:
    """便捷函数：检查基准健康度。"""
    checker = BaselineHealthChecker()
    return checker.check()
