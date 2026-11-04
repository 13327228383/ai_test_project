# -*- coding: utf-8 -*-
"""JSON 显式导入/导出 + Markdown 矩阵导出 — 从 trace_link_store.py 拆出，零业务逻辑变更。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class _IOExportMixin:
    """export_to_json / import_from_json / export_matrix_markdown — 附件级 IO（不参与主链路）。"""

    # -----------------------------------------------------------------
    # 导出 / 导入（让 trace_links.json 仅作显式导入导出附件）
    # -----------------------------------------------------------------
    def export_to_json(self, save_path: str) -> dict[str, Any]:
        links = self.load_links()
        serializable = dict(links)
        serializable["_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        serializable["_count"] = len(links)
        result = {
            "success": False,
            "path": str(save_path),
            "count": len(links),
            "error": "",
        }
        try:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["success"] = True
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result

    def import_from_json(self, json_path: str, replace: bool = False) -> dict[str, Any]:
        result = {"success": False, "path": str(json_path), "count": 0, "error": ""}
        try:
            text = Path(json_path).read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception as e:
            result["error"] = "读取/解析 JSON 失败: %s" % str(e)[:120]
            self._last_error = result["error"]
            return result
        links = {
            k: v
            for k, v in data.items()
            if not k.startswith("_") and isinstance(v, dict) and v.get("req_id")
        }
        result["count"] = len(links)
        if not links:
            result["error"] = "JSON 中没有可导入的追溯链路"
            return result
        if replace:
            if not self.clear_project_links():
                result["error"] = "清空旧 trace_links 失败，中断导入"
                return result
        if self.upsert_links(links):
            result["success"] = True
        else:
            result["error"] = self._last_error or "upsert_links 失败"
        return result

    # -----------------------------------------------------------------
    # Markdown 矩阵导出（供报告与前端预览）
    # -----------------------------------------------------------------
    def export_matrix_markdown(self) -> str:
        links = self.load_links()
        if not links:
            return "## 需求追溯矩阵\n\n（无追溯链路数据）\n"
        lines = [
            "",
            "## 需求追溯矩阵",
            "",
            "| 需求编号 | 模块 | API数 | DB表数 | 页面数 | 置信度 |",
            "|----------|------|-------|--------|--------|--------|",
        ]
        for rid in sorted(links.keys()):
            link = links[rid]
            lines.append(
                "| %s | %s | %d | %d | %d | %s |"
                % (
                    rid,
                    (link.get("module") or "-")[:20],
                    len(link.get("api_endpoints") or []),
                    len(link.get("db_tables") or {}),
                    len(link.get("figma_pages") or []),
                    link.get("confidence") or "medium",
                )
            )
        lines.append("")
        return "\n".join(lines)
