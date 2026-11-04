# -*- coding: utf-8 -*-
"""★ v4: SemanticPatternRenderer — 语义模式渲染器。

将 EntityResolver 返回的源数据渲染为高密度语义文本，直接注入 Prompt。
支持 prompt 预算控制，按优先级截断。

渲染格式与 Phase 1 的 _format_api_chunk / _format_db_chunk 保持一致：
    - API: METHOD /path (query: fields) (body: fields) → response_fields
    - DB:  table_name(col1 pk, col2, col3→ref.table.col, ...)
    - Figma: [TYPE] name "text"  (id: node_id)
"""

import logging

_log = logging.getLogger(__name__)

# 审计列名单（通用，不写死业务列名）
_AUDIT_COLS = {
    "created_at",
    "updated_at",
    "create_time",
    "update_time",
    "create_by",
    "update_by",
    "creator",
    "updater",
    "created_date",
    "updated_date",
    "create_user",
    "update_user",
}


class SemanticPatternRenderer:
    """★ v4: 语义模式渲染器。

    将 EntityResolver 返回的源数据渲染为高密度语义文本。
    支持 prompt 预算控制，按 section 分配字符额度。
    """

    def render(
        self, resolved_data: dict[str, list[dict]], max_chars: int = None
    ) -> str:
        """主入口：渲染 resolved_data 为语义文本。

        Args:
            resolved_data: EntityResolver.resolve() 的返回值
            max_chars: prompt 预算（字符数），None 使用默认值

        Returns:
            渲染后的语义文本，各 section 以空行分隔
        """
        if max_chars is None:
            max_chars = self._get_max_chars()

        sections = []
        # 每个 section 分配 1/3 预算
        per_section = max_chars // 3 if max_chars > 0 else 1500

        api_endpoints = resolved_data.get("api_endpoints", [])
        if api_endpoints:
            api_text = self._render_api_section(api_endpoints, per_section)
            if api_text:
                sections.append(api_text)

        db_tables = resolved_data.get("db_tables", [])
        if db_tables:
            db_text = self._render_db_section(db_tables, per_section)
            if db_text:
                sections.append(db_text)

        figma_nodes = resolved_data.get("figma_nodes", [])
        if figma_nodes:
            figma_text = self._render_figma_section(figma_nodes, per_section)
            if figma_text:
                sections.append(figma_text)

        return "\n\n".join(sections)

    def _render_api_section(self, endpoints: list[dict], max_chars: int) -> str:
        """渲染 API 端点 section。"""
        if not endpoints:
            return ""
        lines = ["【API 端点】"]
        total = len(lines[0])
        for ep in endpoints:
            method = ep.get("method", "?")
            path = ep.get("path", "?")
            sp = ep.get("_structured_params", {})

            req_parts = [f"{method} {path}"]
            if sp.get("req_query_fields"):
                req_parts.append(f"(query: {', '.join(sp['req_query_fields'])})")
            if sp.get("req_body_fields"):
                req_parts.append(f"(body: {', '.join(sp['req_body_fields'])})")

            resp = ", ".join(sp.get("resp_fields", []))
            line = " ".join(req_parts)
            if resp:
                line += f" → {resp}"

            meta = []
            if ep.get("category"):
                meta.append(ep["category"])
            if ep.get("summary"):
                meta.append(ep["summary"])
            if meta:
                line += f"  # {' | '.join(meta)}"

            lines.append(line)
            total += len(line)
            if total > max_chars:
                lines.append(f"... (共 {len(endpoints)} 个端点，已按预算截断)")
                break

        return "\n".join(lines)

    def _render_db_section(self, tables: list[dict], max_chars: int) -> str:
        """渲染 DB 表结构 section。"""
        if not tables:
            return ""
        lines = ["【数据库表】"]
        total = len(lines[0])
        for table in tables:
            tname = table.get("name", "?")
            col_details = table.get("col_details", [])

            col_strs = []
            for c in col_details:
                cname = c.get("name", "")
                if not cname or cname.lower() in _AUDIT_COLS:
                    continue
                is_pk = c.get("is_pk")
                col_strs.append(f"{cname} pk" if is_pk else cname)

            fks = table.get("foreign_keys") or []
            fk_map = {}
            for fk in fks:
                col = fk.get("col", fk.get("column", ""))
                if col:
                    fk_map[col] = f"{fk.get('ref_table', '?')}.{fk.get('ref_col', '?')}"

            final_cols = []
            for cs in col_strs:
                cname = cs.replace(" pk", "")
                if cname in fk_map:
                    final_cols.append(f"{cname}→{fk_map[cname]}")
                else:
                    final_cols.append(cs)

            line = f"{tname}({', '.join(final_cols)})"
            lines.append(line)
            total += len(line)
            if total > max_chars:
                lines.append(f"... (共 {len(tables)} 张表，已按预算截断)")
                break

        return "\n".join(lines)

    def _render_figma_section(self, nodes: list[dict], max_chars: int) -> str:
        """渲染 Figma 节点 section。"""
        if not nodes:
            return ""
        lines = ["【Figma 节点】"]
        total = len(lines[0])
        for node in nodes:
            node_id = node.get("node_id", "?")
            name = node.get("name", "?")
            node_type = node.get("node_type", "")
            text = node.get("text_content", "")

            line = f"[{node_type}] {name}"
            if text:
                line += f' "{text[:50]}"'
            line += f"  (id: {node_id})"

            lines.append(line)
            total += len(line)
            if total > max_chars:
                lines.append(f"... (共 {len(nodes)} 个节点，已按预算截断)")
                break

        return "\n".join(lines)

    def _get_max_chars(self) -> int:
        """获取 prompt 预算（字符数）。"""
        # ★ v4: 从 pipeline_config 读取（用户级 DB 配置 > 环境变量 > 默认值）
        try:
            from src.core.pipeline_config import pipeline_config

            return pipeline_config.rag_prompt_budget
        except Exception as e:
            _log.warning("Prompt预算读取异常: %s", e, exc_info=True)
            return 4000
