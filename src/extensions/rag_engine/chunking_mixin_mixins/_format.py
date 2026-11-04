# -*- coding: utf-8 -*-
"""格式化 mixin"""

import logging
from src.extensions.rag_engine.semantic_renderer import _AUDIT_COLS as _AUDIT_COLS  # noqa: F401  词表唯一源

_log = logging.getLogger("rag_engine")


class ChunkingFormatMixin:
    """文本格式化相关方法。"""

    def _format_api_chunk(self, ep: dict) -> str:
        """★ v4: 语义模式渲染——高密度、低噪音、全量保留。

        输出格式: METHOD /path (query: fields?) (body: fields) → response_fields
        规则:
          - (query: ...) 查询参数
          - (body: ...) 请求体字段
          - → 分隔请求和响应
          - {field1,field2} 嵌套对象字段
          - [item_fields] 数组响应
        """
        method = ep.get("method", "?")
        path = ep.get("path", "?")

        # 构建请求端
        req_parts = [f"{method} {path}"]

        # 使用 v4 结构化参数（如果有），退化到旧字段
        sp = ep.get("_structured_params")
        if sp:
            if sp.get("req_query_fields"):
                req_parts.append(f"(query: {', '.join(sp['req_query_fields'])})")
            if sp.get("req_body_fields"):
                req_parts.append(f"(body: {', '.join(sp['req_body_fields'])})")
        else:
            # 向后兼容：旧 chunk 没有 _structured_params
            rp = ep.get("request_params", "")
            if rp and rp.strip() and rp not in ("[]", "{}"):
                req_parts.append(f"(params: {rp[:80]})")

        req_str = " ".join(req_parts)

        # 构建响应端
        resp_str = ""
        if sp and sp.get("resp_fields"):
            resp_str = ", ".join(sp["resp_fields"])
        elif ep.get("response") and str(ep.get("response")).strip() not in (
            "",
            "[]",
            "{}",
        ):
            resp_str = str(ep.get("response"))[:80]

        # 组装
        main = f"{req_str} → {resp_str}" if resp_str else req_str

        # 元信息（模块/功能/描述）
        meta_parts = []
        if ep.get("category"):
            meta_parts.append(f"模块: {ep['category']}")
        if ep.get("summary"):
            meta_parts.append(f"功能: {ep['summary']}")

        if meta_parts:
            return main + "\n" + " | ".join(meta_parts)
        return main

    def _format_db_chunk(self, table: dict) -> str:
        """★ v4: 语义模式渲染——一行紧凑格式，全量列名不截断。

        输出格式: table_name(col1 pk, col2, col3→ref.table.col, ...)
        规则:
          - pk 标记主键
          - → 标记外键关系（从 meta 的 foreign_keys 读取）
          - 省略纯审计列: created_at, updated_at, create_time, update_time, create_by, update_by
          - 不截断列数（全量保留）
        """
        tname = table.get("name", "?")


        # 收集列名
        col_details = table.get("col_details")
        if col_details:
            col_strs = []
            for c in col_details:
                cname = c.get("name", c.get("COLUMN_NAME", ""))
                if not cname:
                    continue
                # 跳过纯审计列
                if cname.lower() in _AUDIT_COLS:
                    continue
                # 标记主键
                is_pk = c.get("is_pk") or c.get("COLUMN_KEY") == "PRI"
                suffix = " pk" if is_pk else ""
                col_strs.append(f"{cname}{suffix}")
        else:
            # 回退到 columns 纯列名
            col_strs = [
                c
                for c in (table.get("columns", []) or [])
                if isinstance(c, str) and c.lower() not in _AUDIT_COLS
            ]

        # 外键
        fks = table.get("foreign_keys") or []
        fk_map = {}
        for fk in fks:
            col = fk.get("col", fk.get("column", ""))
            ref = f"{fk.get('ref_table', '?')}.{fk.get('ref_col', '?')}"
            if col:
                fk_map[col] = ref

        # 合并：列名中带外键标记
        final_cols = []
        for cs in col_strs:
            # 提取纯列名（去掉 pk 后缀）
            cname = cs.replace(" pk", "")
            if cname in fk_map:
                final_cols.append(f"{cname}→{fk_map[cname]}")
            else:
                final_cols.append(cs)

        return f"{tname}({', '.join(final_cols)})"

    @staticmethod
    def _format_page_chunk(page: dict) -> str:
        """格式化页面文本。"""
        parts = [f"页面: {page.get('name', '?')}"]
        if page.get("module"):
            parts.append(f"所属模块: {page['module']}")
        if page.get("navigation_path"):
            parts.append(f"导航路径: {page['navigation_path']}")
        if page.get("key_elements"):
            parts.append(
                f"关键元素: {', '.join(str(e) for e in page['key_elements'] if e)}"
            )
        return "\n".join(parts)
