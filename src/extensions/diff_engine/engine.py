# -*- coding: utf-8 -*-
from __future__ import annotations

"""DiffEngine 主类 — 从 diff_engine.py 拆出，零逻辑变更。"""

import re
import logging
from datetime import datetime
from typing import Any

from .models import DiffEntry, DiffReport

_log = logging.getLogger(__name__)


class DiffEngine:
    """多维度数据对比引擎。★ 全走 MySQL diff_snapshots，不再有磁盘 _root/versions_dir。"""

    # ═══════════════════════════════════════════
    # 快照管理
    # ═══════════════════════════════════════════

    def save_snapshot(self, data: Any, snapshot_type: str) -> str | None:
        """保存当前数据快照（事实源：MySQL diff_snapshots）。

        返回写入成功的 version_label；失败返回 None。
        """
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        version_label = diff_snapshot_store.save_snapshot(snapshot_type, data)
        if version_label:
            _log.info(
                "[DiffEngine] 快照已保存 (MySQL): %s/%s"
                % (version_label, snapshot_type)
            )
        return version_label

    def load_latest_snapshot(self, snapshot_type: str) -> Any | None:
        """加载最新的快照数据（事实源：MySQL diff_snapshots）。"""
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        data = diff_snapshot_store.load_snapshot(snapshot_type)
        if data is not None:
            _log.info("[DiffEngine] 加载快照 (MySQL): %s", snapshot_type)
            return data
        return None

    def cleanup_old_versions(self, keep_count: int = 5) -> int:
        """清理旧版本，仅保留最近 N 个（事实源：MySQL diff_snapshots）。"""
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        removed = diff_snapshot_store.cleanup_old_versions(keep_count=keep_count)
        if removed:
            _log.info("[DiffEngine] 清理 %d 条旧快照 (MySQL)", removed)
        return removed

    # ═══════════════════════════════════════════
    # 需求 diff
    # ═══════════════════════════════════════════

    def diff_requirements(
        self,
        new_reqs: list[dict],
        old_reqs: list[dict] = None,
    ) -> DiffReport:
        """
        需求变更对比。优先用 AI 做语义 diff，
        回退到 ID-based 结构 diff。
        """
        report = DiffReport(timestamp=datetime.now().isoformat())

        if not old_reqs:
            # 首次运行，所有需求都是新增
            for req in new_reqs:
                report.entries.append(
                    DiffEntry(
                        entity_type="requirement",
                        entity_id=req.get("req_id", "?"),
                        change_type="added",
                        new_value=req.get("description", "")[:100],
                        details=f"新增需求: {req.get('description', '')[:80]}",
                    )
                )
            report.summary = self._build_summary(report)
            return report

        # 结构化 diff（基于 req_id）
        old_map = {r.get("req_id", ""): r for r in old_reqs if r.get("req_id")}
        new_map = {r.get("req_id", ""): r for r in new_reqs if r.get("req_id")}

        old_ids = set(old_map.keys())
        new_ids = set(new_map.keys())

        # 新增
        for rid in new_ids - old_ids:
            r = new_map[rid]
            report.entries.append(
                DiffEntry(
                    entity_type="requirement",
                    entity_id=rid,
                    change_type="added",
                    new_value=r.get("description", "")[:100],
                    details=f"新增需求 [{r.get('module', '')}]: {r.get('description', '')[:80]}",
                )
            )

        # 删除
        for rid in old_ids - new_ids:
            r = old_map[rid]
            report.entries.append(
                DiffEntry(
                    entity_type="requirement",
                    entity_id=rid,
                    change_type="removed",
                    old_value=r.get("description", "")[:100],
                    details=f"删除需求 [{r.get('module', '')}]: {r.get('description', '')[:80]}",
                )
            )

        # 修改
        for rid in old_ids & new_ids:
            old_r = old_map[rid]
            new_r = new_map[rid]
            if old_r.get("description", "") != new_r.get(
                "description", ""
            ) or old_r.get("module", "") != new_r.get("module", ""):
                report.entries.append(
                    DiffEntry(
                        entity_type="requirement",
                        entity_id=rid,
                        change_type="modified",
                        old_value=old_r.get("description", "")[:100],
                        new_value=new_r.get("description", "")[:100],
                        details=f"需求变更 [{new_r.get('module', '')}]: 描述变化",
                    )
                )

        report.summary = self._build_summary(report)
        if report.has_changes:
            _log.info(
                "[DiffEngine] 需求变更: +%d -%d ~%d"
                % (report.added_count, report.removed_count, report.modified_count)
            )
        else:
            _log.info("[DiffEngine] 需求无变更")
        return report

    # ═══════════════════════════════════════════
    # API schema diff
    # ═══════════════════════════════════════════

    def diff_api_schema(
        self,
        new_yapi: dict,
        old_yapi: dict = None,
    ) -> DiffReport:
        """API schema 变更对比（结构化 diff）。"""
        report = DiffReport(timestamp=datetime.now().isoformat())

        new_endpoints = self._extract_endpoint_keys(new_yapi)
        old_endpoints = self._extract_endpoint_keys(old_yapi) if old_yapi else {}

        new_keys = set(new_endpoints.keys())
        old_keys = set(old_endpoints.keys())

        for key in new_keys - old_keys:
            ep = new_endpoints[key]
            report.entries.append(
                DiffEntry(
                    entity_type="api_endpoint",
                    entity_id=key,
                    change_type="added",
                    new_value={"method": ep.get("method"), "path": ep.get("path")},
                    details=f"新增接口: {ep.get('method','?')} {ep.get('path','?')}",
                )
            )

        for key in old_keys - new_keys:
            ep = old_endpoints[key]
            report.entries.append(
                DiffEntry(
                    entity_type="api_endpoint",
                    entity_id=key,
                    change_type="removed",
                    old_value={"method": ep.get("method"), "path": ep.get("path")},
                    details=f"删除接口: {ep.get('method','?')} {ep.get('path','?')}",
                )
            )

        for key in old_keys & new_keys:
            old_ep = old_endpoints[key]
            new_ep = new_endpoints[key]
            changes = self._diff_endpoint_fields(old_ep, new_ep)
            for change in changes:
                report.entries.append(
                    DiffEntry(
                        entity_type="api_endpoint",
                        entity_id=key,
                        change_type="modified",
                        old_value=change.get("old"),
                        new_value=change.get("new"),
                        details=change.get("detail", ""),
                    )
                )

        report.summary = self._build_summary(report)
        if report.has_changes:
            _log.info(
                "[DiffEngine] API 变更: +%d -%d ~%d"
                % (report.added_count, report.removed_count, report.modified_count)
            )
        else:
            _log.info("[DiffEngine] API 无变更")
        return report

    # ═══════════════════════════════════════════
    # DB schema diff
    # ═══════════════════════════════════════════

    def diff_db_schema(
        self,
        new_schema: str,
        old_schema: str = "",
    ) -> DiffReport:
        """DB schema 变更对比（结构化 diff）。"""
        report = DiffReport(timestamp=datetime.now().isoformat())

        new_tables = self._parse_schema_tables(new_schema)
        old_tables = self._parse_schema_tables(old_schema) if old_schema else {}

        new_names = set(new_tables.keys())
        old_names = set(old_tables.keys())

        for tname in new_names - old_names:
            report.entries.append(
                DiffEntry(
                    entity_type="db_table",
                    entity_id=tname,
                    change_type="added",
                    details=f"新增表: {tname}",
                )
            )

        for tname in old_names - new_names:
            report.entries.append(
                DiffEntry(
                    entity_type="db_table",
                    entity_id=tname,
                    change_type="removed",
                    details=f"删除表: {tname}",
                )
            )

        for tname in old_names & new_names:
            old_cols = set(old_tables.get(tname, []))
            new_cols = set(new_tables.get(tname, []))

            for col in new_cols - old_cols:
                report.entries.append(
                    DiffEntry(
                        entity_type="db_column",
                        entity_id=f"{tname}.{col}",
                        change_type="added",
                        details=f"表 {tname} 新增列: {col}",
                    )
                )

            for col in old_cols - new_cols:
                report.entries.append(
                    DiffEntry(
                        entity_type="db_column",
                        entity_id=f"{tname}.{col}",
                        change_type="removed",
                        details=f"表 {tname} 删除列: {col}",
                    )
                )

        report.summary = self._build_summary(report)
        if report.has_changes:
            _log.info(
                "[DiffEngine] DB Schema 变更: +%d -%d ~%d"
                % (report.added_count, report.removed_count, report.modified_count)
            )
        else:
            _log.info("[DiffEngine] DB Schema 无变更")
        return report

    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    @staticmethod
    def _extract_endpoint_keys(yapi_data: dict) -> dict[str, dict]:
        """从 YApi 数据中提取 endpoint key → 详情映射。"""
        endpoints = {}
        if not yapi_data:
            return endpoints

        data = yapi_data.get("data", yapi_data)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("list", data.get("apis", []))
        else:
            return endpoints

        for item in items:
            if isinstance(item, dict):
                method = str(item.get("method", "")).upper()
                path = item.get("path", "")
                key = f"{method}:{path}"
                endpoints[key] = {
                    "method": method,
                    "path": path,
                    "title": item.get("title", ""),
                    "req_params": str(
                        item.get("req_params", item.get("query_path", {}))
                    )[:500],
                    "res_body": str(item.get("res_body", item.get("response", {})))[
                        :500
                    ],
                }
        return endpoints

    @staticmethod
    def _diff_endpoint_fields(old_ep: dict, new_ep: dict) -> list[dict]:
        """对比单个 endpoint 的字段变化。"""
        changes = []
        for field in ["title", "req_params", "res_body"]:
            old_val = old_ep.get(field, "")
            new_val = new_ep.get(field, "")
            if old_val != new_val:
                changes.append(
                    {
                        "field": field,
                        "old": old_val[:100],
                        "new": new_val[:100],
                        "detail": f"{old_ep.get('method','?')} {old_ep.get('path','?')} {field} 变更",
                    }
                )
        return changes

    @staticmethod
    def _parse_schema_tables(schema_text: str) -> dict[str, list[str]]:
        """从 schema 文本解析表→列映射。支持中文表名。"""
        tables = {}
        if not schema_text:
            return tables

        # ★ P1: \w 只匹配 ASCII，扩展为支持中文（一-鿿）
        pattern = re.compile(r"[-*]\s*([\w一-鿿]+)\s*[:：]\s*(.+)", re.MULTILINE)
        for m in pattern.finditer(schema_text):
            tname = m.group(1).strip()
            cols_str = m.group(2).strip()
            cols = re.findall(r"([\w一-鿿]+)\s*\(", cols_str)
            if tname and not tname.startswith("_"):
                tables[tname] = cols
        return tables

    @staticmethod
    def _build_summary(report: "DiffReport") -> dict:
        """汇总变更统计。"""
        by_type = {}
        for e in report.entries:
            ct = e.change_type
            et = e.entity_type
            if et not in by_type:
                by_type[et] = {"added": 0, "removed": 0, "modified": 0}
            by_type[et][ct] += 1

        return {
            "total_changes": len(report.entries),
            "added": report.added_count,
            "removed": report.removed_count,
            "modified": report.modified_count,
            "by_entity_type": by_type,
        }

    # ═══════════════════════════════════════════
    # 报告格式化
    # ═══════════════════════════════════════════

    def format_diff_report(self, report: DiffReport) -> str:
        """格式化为 Markdown 报告。"""
        if not report.has_changes:
            return "\n## 变更对比\n\n（无变更）\n"

        lines = [
            "",
            "## 变更对比报告",
            "",
            f"**时间**: {report.timestamp}",
            f"**变更总数**: {report.summary.get('total_changes', 0)}",
            f"**新增**: {report.added_count} | **删除**: {report.removed_count} | **修改**: {report.modified_count}",
            "",
        ]

        # 按类型分组展示
        by_type = {}
        for e in report.entries:
            by_type.setdefault(e.entity_type, []).append(e)

        for etype, entries in by_type.items():
            type_labels = {
                "requirement": "需求变更",
                "api_endpoint": "API 接口变更",
                "db_table": "数据库表变更",
                "db_column": "数据库列变更",
            }
            lines.append(f"### {type_labels.get(etype, etype)}")

            for e in entries:
                icon = {"added": "➕", "removed": "➖", "modified": "✏️"}.get(
                    e.change_type, "•"
                )
                lines.append(f"- {icon} {e.entity_id}: {e.details}")

            lines.append("")

        return "\n".join(lines)
