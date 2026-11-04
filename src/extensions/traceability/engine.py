# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

import os
import logging
from typing import Any

"""
追溯链路引擎 — 引擎主体
======================
TraceabilityEngine：AI 驱动的追溯链路构建器。
整合数据源提取、AI 构建、启发式回退、覆盖率计算、持久化、格式化、查询等能力。

事实源原则：
    - 主链路只依赖 MySQL ``trace_links`` 表，不再把 ``trace_links.json``
      作为运行时兜底；
    - ``trace_links.json`` 仅作显式导入/导出/附件角色，通过
      ``import_from_json(json_path)`` 和 ``export_to_json(path)`` 操作；
    - 报告/前端链路也统一使用 MySQL 数据，通过 ``format_report_section``/
      ``format_trace_matrix_md`` 读取内存结果再拼装。
"""

import logging
from typing import Any

from .models import TraceLink, CoverageReport
from .data_source import DataSourceExtractor
from .ai_builder import AIBuilder
from .heuristic import HeuristicBuilder
from .coverage import TraceabilityCoverage
from .persistence import TraceabilityPersistence
from .formatter import TraceabilityFormatter
from .queries import TraceabilityQueries

_log = logging.getLogger("traceability_engine")


class TraceabilityEngine:
    """AI 驱动的追溯链路构建器。"""

    # 启发式匹配最小关键词长度（低于此长度的子串不参与匹配，防止误报）
    HEURISTIC_MIN_KEYWORD_LEN = 4
    # API 路径匹配最小长度
    API_PATH_MIN_LEN = 4

    def __init__(self, store=None, cache_hit_threshold: float = None):
        # ★ P1: 缓存命中阈值走 pipeline_config (env 覆盖)
        if cache_hit_threshold is None:
            try:
                from src.core.pipeline_config import pipeline_config

                cache_hit_threshold = float(
                    pipeline_config.traceability_cache_hit_threshold
                )
            except Exception:
                cache_hit_threshold = 0.9
        self._links: dict[str, TraceLink] = {}
        self._cache_hit_threshold = max(0.0, min(cache_hit_threshold, 1.0))
        try:
            from src.pipeline.utils.trace_link_store import trace_link_store as _store

            self._store = store or _store
        except Exception:
            _log.warning(
                "[TraceabilityEngine] trace_link_store 加载失败，store 不可用",
                exc_info=True,
            )
            self._store = store

        # 初始化各子模块
        self._persistence = TraceabilityPersistence(self._store)
        self._coverage = TraceabilityCoverage()
        self._formatter = TraceabilityFormatter(self._links)
        self._queries = TraceabilityQueries(self._links)
        self._ai_builder = AIBuilder()
        self._heuristic = HeuristicBuilder()
        self._data_source = DataSourceExtractor()

    # -----------------------------------------------------------------
    # 事实源层：主路径只读写 MySQL；JSON 作为显式导入/导出
    # -----------------------------------------------------------------
    def import_from_json(self, json_path: str, replace: bool = False) -> dict[str, Any]:
        """显式把 ``trace_links.json`` 回填到 MySQL（不作为运行时兜底）。"""
        return self._persistence.import_from_json(json_path, replace=replace)

    def export_to_json(self, save_path: str) -> dict[str, Any]:
        """显式从 MySQL trace_links 导出 JSON 附件。"""
        return self._persistence.export_to_json(save_path)

    # ═══════════════════════════════════════════
    # 主入口：构建追溯链路
    # ═══════════════════════════════════════════

    def build_trace_links(
        self,
        requirements: list[dict],
        yapi_specs: dict = None,
        db_schema: str = "",
        force_refresh: bool = False,
    ) -> dict[str, TraceLink]:
        """为所有需求构建追溯链路。主事实源：MySQL ``trace_links`` 表。"""
        # 优先从 MySQL 加载缓存（不再把 trace_links.json 作为运行时兜底）
        if not force_refresh:
            cached = self._persistence.load_from_db()
            if (
                cached
                and len(cached) >= max(len(requirements), 1) * self._cache_hit_threshold
            ):
                cached_ids = set(cached.keys())
                req_ids = {
                    str(r.get("req_id", "")) for r in requirements if r.get("req_id")
                }
                if not req_ids or req_ids.issubset(cached_ids):
                    self._links = cached
                    _log.debug(
                        "[TraceabilityEngine] 使用 MySQL 追溯链路 (%d 条)", len(cached)
                    )
                    self._update_submodule_links()
                    return cached

        _log.debug(
            "[TraceabilityEngine] 构建追溯链路 (%d 条需求)...", len(requirements)
        )

        # 预处理数据源
        api_list = self._data_source.extract_api_list(yapi_specs or {})
        table_list = self._data_source.parse_db_tables(db_schema)
        page_list = self._data_source.extract_page_list()

        # ★ 根因修复 — 外部数据源（YApi/DB schema/Figma）全空时，
        # 自动扫描靶子系统源码（Flask 路由 / SQLite CREATE TABLE / SQLAlchemy Model）兜底。
        # 否则 smartshop/api_demo 这类纯 Flask 靶子永远只有 pages，API/DB 全空。
        if not api_list and not table_list:
            with guard("build_trace_links: from src.core.config_loader import c", log=_log, level=logging.WARNING):
                from src.core.config_loader import config as _cfg
                project_name = getattr(_cfg, "project_name", "") or ""
                # 尝试两个路径：examples/{name} 和 {name}
                candidates = [
                    "examples/" + project_name,
                    project_name,
                ]
                target_dir = next(
                    (c for c in candidates if os.path.exists(c)), ""
                )
                if target_dir:
                    scan = self._data_source.scan_target_code(target_dir)
                    # 外部数据源优先（有就覆盖），scan 兜底填充空的
                    if not api_list:
                        api_list = scan["api_list"]
                    if not table_list:
                        table_list = scan["table_list"]
                    if not page_list:
                        page_list = scan["page_list"]
                    _log.info(
                        "  [TraceabilityEngine] scan_target_code ✅ API=%d DB=%d Pages=%d",
                        len(api_list), len(table_list), len(page_list),
                    )

        # 批量 AI 构建
        if api_list or table_list or page_list:
            result = self._ai_builder.build_links(
                requirements, api_list, table_list, page_list
            )
        else:
            _log.debug("  [TraceabilityEngine] 无数据源，使用启发式回退")
            result = self._heuristic.build_links(
                requirements, table_list, page_list, api_list
            )

        self._links = result
        self._update_submodule_links()
        # 主事实源：写入 MySQL；trace_links.json 不再作为运行时写入
        self._persistence.save_to_db(result)
        _log.debug("[TraceabilityEngine] 追溯链路构建完成: %d 条", len(result))
        return result

    def _update_submodule_links(self):
        """同步 _links 到各子模块。"""
        self._formatter._links = self._links
        self._queries._links = self._links

    # ═══════════════════════════════════════════
    # 数据源覆盖率计算（委托给 coverage 子模块）
    # ═══════════════════════════════════════════

    def compute_all_coverage(
        self,
        trace_links: dict[str, TraceLink] = None,
        test_cases: list[dict] = None,
    ) -> CoverageReport:
        """计算全部数据源维度的覆盖率。"""
        return self._coverage.compute_all_coverage(
            trace_links or self._links, test_cases
        )

    def compute_api_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        return self._coverage.compute_api_coverage(links, intents)

    def compute_db_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        return self._coverage.compute_db_coverage(links, intents)

    def compute_page_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        return self._coverage.compute_page_coverage(links, intents)

    def compute_page_coverage_incremental(
        self,
        links: dict[str, TraceLink],
        intents: list[dict],
        previous: dict[str, Any] | None = None,
        affected_pages: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._coverage.compute_page_coverage_incremental(
            links, intents, previous, affected_pages
        )

    def compute_trace_completeness(self, links: dict[str, TraceLink]) -> dict[str, Any]:
        return self._coverage.compute_trace_completeness(links)

    def _build_coverage_summary(self, report: CoverageReport) -> dict:
        return self._coverage._build_coverage_summary(report)

    # ═══════════════════════════════════════════
    # 报告格式化（委托给 formatter 子模块）
    # ═══════════════════════════════════════════

    def format_report_section(self, coverage_report: CoverageReport) -> str:
        """将覆盖率报告格式化为 Markdown 章节（注入 Step5 报告）。"""
        return self._formatter.format_report_section(coverage_report)

    def format_trace_matrix_md(self) -> str:
        """格式化所有追溯链路为 Markdown 矩阵。"""
        return self._formatter.format_trace_matrix_md()

    # ═══════════════════════════════════════════
    # 持久化（委托给 persistence 子模块）
    # ═══════════════════════════════════════════

    def load_cached_links(self) -> dict[str, TraceLink] | None:
        """从事实源（MySQL trace_links 表）加载已保存的追溯链路。"""
        return self._persistence.load_cached_links()

    def save_trace_links(self, links: dict[str, TraceLink] = None) -> bool:
        """保存追溯链路：主路径写入 MySQL，不再生成 JSON 文件。"""
        if links is not None:
            self._links = links
            self._update_submodule_links()
        return self._persistence.save_trace_links(self._links)

    def _load_cache(self) -> dict[str, TraceLink] | None:
        """兼容旧入口：直接转发到 MySQL 加载，不再自动读 JSON 文件。"""
        return self._persistence.load_cache()

    def _save_cache(self, links: dict[str, TraceLink]) -> None:
        """兼容旧入口：只写入 MySQL，不再生成 JSON 文件。"""
        self._persistence.save_cache(links)

    # ═══════════════════════════════════════════
    # 对外查询接口（委托给 queries 子模块）
    # ═══════════════════════════════════════════

    def get_apis_for_req(self, req_id: str) -> list[dict]:
        """查询某需求关联的 API 列表。"""
        return self._queries.get_apis_for_req(req_id)

    def get_tables_for_req(self, req_id: str) -> dict[str, list[str]]:
        """查询某需求关联的数据库表。"""
        return self._queries.get_tables_for_req(req_id)

    def get_pages_for_req(self, req_id: str) -> list[str]:
        """查询某需求关联的页面。"""
        return self._queries.get_pages_for_req(req_id)

    def find_reqs_by_api(self, method: str, path: str) -> list[str]:
        """根据 API 反向查找关联的需求。"""
        return self._queries.find_reqs_by_api(method, path)

    def find_reqs_by_table(self, table_name: str) -> list[str]:
        """根据数据库表反向查找关联的需求。"""
        return self._queries.find_reqs_by_table(table_name)

    def get_feature_id_for_req(self, req_id: str) -> int:
        """查询某需求关联的功能清单ID（→ system_feature_list.id）。"""
        return self._queries.get_feature_id_for_req(req_id)

    def find_reqs_by_feature(self, feature_id: int) -> list[str]:
        """根据功能清单ID反向查找关联的需求列表。"""
        return self._queries.find_reqs_by_feature(feature_id)

    # =====================================================================
    # ★ 向后兼容别名（f3995cc4 拆分后旧名 → 重用 / 简单实现）
    # =====================================================================

    @staticmethod
    def _extract_api_list(yapi: dict) -> list[dict]:
        """[兼容别名] 从 YApi 数据提取 API 列表。"""
        apis = []
        data = yapi.get("data", []) if isinstance(yapi, dict) else []
        if isinstance(data, list):
            apis = [
                {
                    "method": item.get("method", ""),
                    "path": item.get("path", ""),
                    "summary": item.get("title", ""),
                }
                for item in data
                if isinstance(item, dict)
            ]
        return apis

    @staticmethod
    def _extract_page_list() -> list[dict]:
        """从 figma_pages 表提取页面列表（★ 2026-06-30: 替代 nav_map）。"""
        return DataSourceExtractor.extract_page_list()

    @staticmethod
    def _parse_db_tables(db_schema: str) -> list[dict]:
        """[兼容别名] 解析 DB schema 文本提取表名和列。

        支持格式：- 或 * 前缀 + 英文/中文冒号；下划线开头表名自动过滤。
        """
        import re

        tables = []
        if not db_schema:
            return tables
        pattern = re.compile(r"^[-*]\s*(\w+)\s*[：:]\s*(.+)")
        for line in str(db_schema).split("\n"):
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                tname = m.group(1)
                if tname.startswith("_"):
                    continue
                cols_raw = m.group(2)
                cols = [
                    c.strip().split("(")[0].split("（")[0].strip()
                    for c in cols_raw.split(",")
                ]
                tables.append({"name": tname, "columns": cols})
        return tables

    def _heuristic_single_link(
        self, req: dict, table_list: list[dict], page_list: list[dict]
    ):
        """[兼容别名] 对单条需求做关键词启发式匹配。"""
        from src.extensions.traceability.heuristic import HeuristicBuilder

        h = HeuristicBuilder()
        return h._heuristic_single_link(req, table_list, page_list)


# 单例（全走 MySQL trace_links，无需 project_root）
# ★ 方案C 改造：traceability_engine 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

traceability_engine = PerProjectRegistry(TraceabilityEngine)
