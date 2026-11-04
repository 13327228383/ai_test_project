# -*- coding: utf-8 -*-
"""解析 mixin"""

import re
from src.core.guarded import guard
import logging
from typing import Any

_log = logging.getLogger("rag_engine")


class ChunkingParseMixin:
    """文档解析相关方法。"""

    @staticmethod
    def _parse_yapi_endpoints(yapi_specs: dict) -> list[dict]:
        """从 YApi 规范中解析 API endpoint 列表（v2: 含分类/标签）。"""
        endpoints = []
        if not yapi_specs:
            return endpoints

        data = yapi_specs.get("data", yapi_specs)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get(
                "list", data.get("apis", data.get("items", data.get("interfaces", [])))
            )
        else:
            return endpoints

        for item in items:
            if isinstance(item, dict):
                api_meta_truncate = getattr(
                    ChunkingParseMixin, "_API_META_TRUNCATE", 300
                )
                # ★ v2: 提取分类/标签信息
                category = (
                    item.get("catname", "")
                    or item.get("tag", "")
                    or item.get("category", "")
                    or item.get("menu", "")
                )
                # ★ YApi 实际字段: req_query(请求参数) + req_body(请求体) + res_body(响应) + desc(描述)
                # ★ v4: 结构化参数提取（供语义模式渲染使用）
                structured_params = ChunkingParseMixin._parse_api_params_structured(
                    item
                )
                endpoints.append(
                    {
                        "method": str(item.get("method", "")).upper(),
                        "path": item.get("path", ""),
                        "summary": item.get("title", item.get("name", "")),
                        "category": str(category),
                        # ★ v4: 保留旧字段向后兼容（format_retrieval_context 等旧路径仍可读）
                        "request_params": str(
                            item.get(
                                "req_query",
                                item.get("req_params", item.get("query_path", {})),
                            )
                        )[:api_meta_truncate],
                        "request_body": str(item.get("req_body", ""))[
                            :api_meta_truncate
                        ],
                        "response": str(item.get("res_body", item.get("response", {})))[
                            :api_meta_truncate
                        ],
                        "description": str(
                            item.get("desc", item.get("description", ""))
                        )[:200],
                        # ★ v4: 新增结构化参数（供语义模式渲染使用）
                        "_structured_params": structured_params,
                    }
                )
        return endpoints

    @staticmethod
    def _parse_api_params_structured(item: dict) -> dict[str, Any]:
        """★ v4: 从 YApi item 中提取结构化的参数/响应字段列表。

        替代 str(dict) 做法，输出可直接渲染为语义模式的字段树。

        返回: {
            "req_query_fields": ["username", "password", ...],
            "req_body_fields": ["username(string)", "password(string)", ...],
            "resp_fields": ["token(string)", "user{id,username,email}", ...],
        }
        """
        import json as _json

        def _expand_field(name: str, schema: Any, depth: int = 0) -> str:
            """递归展开 JSON Schema 字段（最多 2 层嵌套）。"""
            if not isinstance(schema, dict):
                return name
            ftype = schema.get("type", "")
            props = schema.get("properties", {})
            if props and depth < 2:
                inner = [_expand_field(k, v, depth + 1) for k, v in props.items()]
                return f"{name}{{{', '.join(inner)}}}"
            if ftype == "array" and depth < 2:
                items = schema.get("items", {})
                if isinstance(items, dict) and items.get("properties"):
                    inner = [
                        _expand_field(k, v, depth + 1)
                        for k, v in items["properties"].items()
                    ]
                    return f"{name}[{', '.join(inner)}]"
                return (
                    f"{name}[{items.get('type', '')}]"
                    if isinstance(items, dict)
                    else f"{name}[]"
                )
            return f"{name}({ftype})" if ftype else name

        result: dict[str, Any] = {
            "req_query_fields": [],
            "req_body_fields": [],
            "resp_fields": [],
        }

        # 1. req_query
        rq = item.get("req_query", item.get("req_params", item.get("query_path")))
        if rq:
            if isinstance(rq, list):
                for q in rq:
                    if isinstance(q, dict) and q.get("name"):
                        result["req_query_fields"].append(q["name"])
                    elif isinstance(q, str) and q:
                        result["req_query_fields"].append(q)
            elif isinstance(rq, dict):
                props = rq.get("properties", {})
                result["req_query_fields"] = (
                    list(props.keys()) if props else list(rq.keys())
                )

        # 2. req_body（优先 req_body_form，其次 JSON Schema）
        rbf = item.get("req_body_form")
        if isinstance(rbf, list) and rbf:
            for f in rbf:
                if isinstance(f, dict) and f.get("name"):
                    ft = f.get("type", "")
                    result["req_body_fields"].append(
                        f"{f['name']}({ft})" if ft else f["name"]
                    )
        else:
            rb = item.get("req_body", "")
            if isinstance(rb, str) and rb.strip():
                try:
                    rb = _json.loads(rb)
                except Exception as _je:
                    _log.debug(
                        "[parse] req_body JSON解析失败(用None): %s", str(_je)[:50]
                    )
                    rb = None
            if isinstance(rb, dict):
                props = rb.get("properties", {})
                if props:
                    for name, schema in props.items():
                        ft = schema.get("type", "") if isinstance(schema, dict) else ""
                        result["req_body_fields"].append(
                            f"{name}({ft})" if ft else name
                        )

        # 3. res_body（JSON Schema 递归展开）
        res = item.get("res_body", item.get("response"))
        if isinstance(res, str) and res.strip():
            try:
                res = _json.loads(res)
            except Exception:
                res = None
        if isinstance(res, dict):
            props = res.get("properties", {})
            if props:
                for name, schema in props.items():
                    result["resp_fields"].append(_expand_field(name, schema))
            elif res.get("type") == "array":
                # 顶层是数组 → 展开 items 的 properties
                items = res.get("items", {})
                if isinstance(items, dict) and items.get("properties"):
                    inner = [
                        _expand_field(k, v) for k, v in items["properties"].items()
                    ]
                    result["resp_fields"].append(f"[{', '.join(inner)}]")

        return result

    @staticmethod
    def _parse_db_tables(db_schema: str) -> list[dict]:
        """从 DB schema 文本或实时数据库解析表结构（v2: 含类型/注释/外键）。

        优先查询真实数据库的 information_schema，失败时回退到文本正则解析。
        """
        # ★ v2: 优先实时查询数据库元数据
        tables = ChunkingParseMixin._parse_db_from_live()
        if tables:
            return tables

        # 回退：文本正则解析
        tables = []
        if not db_schema:
            return tables

        table_pattern = re.compile(r"^[-*]\s*(\w+)\s*[:：]\s*(.+)", re.MULTILINE)
        for m in table_pattern.finditer(db_schema):
            tname = m.group(1).strip()
            cols_str = m.group(2).strip()
            cols = re.findall(r"(\w+)\s*\(", cols_str)
            if tname and not tname.startswith("_"):
                # ★ v4: 全量列，压缩在 _format_db_chunk 做
                tables.append({"name": tname, "columns": cols})
        return tables

    @staticmethod
    def _parse_db_from_live() -> list[dict]:
        """★ v3: 从目标被测系统 DB 查询表结构（列名/类型/注释）和外键。

        使用 db_agent 直连目标 DB（非项目自身 MySQL），支持 MySQL/PostgreSQL/SQLite。
        """
        tables = []
        with guard("_parse_db_from_live: from src.core.config_loader import c", log=_log, level=logging.WARNING):
            from src.core.config_loader import config as _cfg

            db_enabled = getattr(_cfg, "db_enabled", False)
            if not db_enabled:
                _log.info(
                    "[RagEngine] DB 索引跳过: 目标数据库未启用 (db_enabled=False)"
                )
                return tables
            db_type = str(getattr(_cfg, "db_type", "mysql") or "mysql").lower()
            db_name = getattr(_cfg, "db_name", "") or ""

            from src.extensions.db.db_agent import db_agent

            # 确保已连接目标 DB
            if db_agent._conn is None:
                db_agent.connect()
            if db_agent._conn is None:
                _log.info("[RagEngine] DB 索引跳过: 目标数据库连接失败")
                return tables
            max_tables = 30

            # 1. 获取表列表（按 DB 类型分发）
            if db_type == "mysql":
                if not db_name:
                    _log.info(
                        "[RagEngine] DB 索引跳过: MySQL 目标数据库名 (db_name) 未配置"
                    )
                    return tables
                result = db_agent.query(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' "
                    "ORDER BY TABLE_NAME LIMIT %s",
                    (db_name, max_tables),
                )
            elif db_type == "postgresql":
                result = db_agent.query(
                    "SELECT table_name AS TABLE_NAME FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE' "
                    "ORDER BY table_name LIMIT %s",
                    (max_tables,),
                )
            elif db_type == "sqlite":
                result = db_agent.query(
                    "SELECT name AS TABLE_NAME FROM sqlite_master "
                    "WHERE type='table' ORDER BY name LIMIT %s",
                    (max_tables,),
                )
            elif db_type in ("yasdb", "oracle"):
                result = db_agent.query(
                    "SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME"
                )
            else:
                _log.info("[RagEngine] DB 索引跳过: 不支持的数据库类型 %s", db_type)
                return tables

            if not result.get("success") or not result.get("data"):
                _log.info(
                    "[RagEngine] DB 索引跳过: 目标数据库 %s 查询无结果",
                    db_name or db_type,
                )
                return tables

            table_names = []
            for r in result["data"]:
                tn = r.get("TABLE_NAME", r.get("table_name", ""))
                if tn:
                    table_names.append(tn)

            if not table_names:
                _log.info("[RagEngine] DB 索引跳过: 目标数据库无表")
                return tables

            # 2. ★ v4: 批量查询列信息（替代逐表 DESC/PRAGMA）
            cols_by_table, pks_by_table = ChunkingParseMixin._parse_db_columns_batch(
                table_names, db_type, db_name
            )
            fks_by_table = {}

            # 3. 组装表结构（★ v4: 全量列，不截断）
            for tn in table_names:
                cols = cols_by_table.get(tn, [])
                pks = pks_by_table.get(tn, [])
                fks = fks_by_table.get(tn, [])
                tables.append(
                    {
                        "name": tn,
                        "columns": [c["COLUMN_NAME"] for c in cols],
                        "col_details": [
                            {
                                "name": c["COLUMN_NAME"],
                                "type": c.get("COLUMN_TYPE") or c.get("DATA_TYPE", ""),
                                "comment": (c.get("COLUMN_COMMENT") or "")[:80],
                                "is_pk": c.get("COLUMN_KEY") == "PRI",
                            }
                            for c in cols
                        ],
                        "primary_keys": pks,
                        "foreign_keys": fks,
                    }
                )

            if tables:
                _log.info(
                    "[RagEngine] DB 实时查询完成: %d 表 (目标DB=%s/%s)",
                    len(tables),
                    db_type,
                    db_name or "?",
                )
            else:
                _log.info("[RagEngine] DB 索引跳过: 未能解析任何表结构")

        return tables

    @staticmethod
    def _parse_db_columns_batch(
        table_names: list[str], db_type: str, db_name: str
    ) -> tuple:
        """★ v4: 批量查询列信息（1 次 SQL 替代 N 次 DESC）。

        返回: (cols_by_table, pks_by_table)
        """
        cols_by_table: dict[str, list[dict]] = {}
        pks_by_table: dict[str, list[str]] = {}
        if not table_names:
            return cols_by_table, pks_by_table

        with guard("_parse_db_columns_batch: from src.extensions.db.db_agent impo", log=_log, level=logging.WARNING):
            from src.extensions.db.db_agent import db_agent

            if db_type == "mysql" and db_name:
                # ★ 批量查询：1 次 SQL 替代 N 次 DESC
                ph = ",".join(["%s"] * len(table_names))
                sql = (
                    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, "
                    "COLUMN_COMMENT, COLUMN_KEY, ORDINAL_POSITION "
                    "FROM information_schema.COLUMNS "
                    f"WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ({ph}) "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                )
                result = db_agent.query(sql, (db_name, *table_names))
                if result.get("success") and result.get("data"):
                    for row in result["data"]:
                        tn = row.get("TABLE_NAME", "")
                        col = {
                            "COLUMN_NAME": row.get("COLUMN_NAME", ""),
                            "COLUMN_TYPE": row.get("COLUMN_TYPE", ""),
                            "DATA_TYPE": row.get("DATA_TYPE", ""),
                            "COLUMN_COMMENT": row.get("COLUMN_COMMENT", "") or "",
                            "COLUMN_KEY": row.get("COLUMN_KEY", ""),
                            "ORDINAL_POSITION": row.get("ORDINAL_POSITION", 0),
                        }
                        cols_by_table.setdefault(tn, []).append(col)
                        if col["COLUMN_KEY"] == "PRI":
                            pks_by_table.setdefault(tn, []).append(col["COLUMN_NAME"])

            elif db_type == "postgresql":
                ph = ",".join(["%s"] * len(table_names))
                sql = (
                    "SELECT table_name AS TABLE_NAME, column_name AS COLUMN_NAME, "
                    "data_type AS COLUMN_TYPE, data_type AS DATA_TYPE, "
                    "'' AS COLUMN_COMMENT, '' AS COLUMN_KEY, ordinal_position AS ORDINAL_POSITION "
                    "FROM information_schema.columns "
                    f"WHERE table_schema='public' AND table_name IN ({ph}) "
                    "ORDER BY table_name, ordinal_position"
                )
                result = db_agent.query(sql, tuple(table_names))
                if result.get("success") and result.get("data"):
                    for row in result["data"]:
                        tn = row.get("TABLE_NAME", "")
                        cols_by_table.setdefault(tn, []).append(
                            {
                                "COLUMN_NAME": row.get("COLUMN_NAME", ""),
                                "COLUMN_TYPE": row.get("COLUMN_TYPE", ""),
                                "DATA_TYPE": row.get("DATA_TYPE", ""),
                                "COLUMN_COMMENT": "",
                                "COLUMN_KEY": row.get("COLUMN_KEY", ""),
                                "ORDINAL_POSITION": row.get("ORDINAL_POSITION", 0),
                            }
                        )

            else:
                # SQLite / YasDB / Oracle / 未知: 逐表查询（全量列，不截断）
                for tn in table_names:
                    with guard("_parse_db_columns_batch: if db_type == 'sqlite': col_result =", log=_log):
                        if db_type == "sqlite":
                            col_result = db_agent.query("PRAGMA table_info('%s')" % tn)
                            if col_result.get("success") and col_result.get("data"):
                                cols = []
                                for c in col_result["data"]:
                                    cols.append(
                                        {
                                            "COLUMN_NAME": c.get("name", ""),
                                            "COLUMN_TYPE": c.get("type", ""),
                                            "DATA_TYPE": c.get("type", ""),
                                            "COLUMN_COMMENT": "",
                                            "COLUMN_KEY": "PRI" if c.get("pk") else "",
                                            "ORDINAL_POSITION": c.get("cid", 0),
                                        }
                                    )
                                cols_by_table[tn] = cols
                                pks = [
                                    c["COLUMN_NAME"]
                                    for c in cols
                                    if c["COLUMN_KEY"] == "PRI"
                                ]
                                if pks:
                                    pks_by_table[tn] = pks
                        elif db_type in ("yasdb", "oracle"):
                            col_result = db_agent.query(
                                "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_ID AS ORDINAL_POSITION, "
                                "'' AS COLUMN_COMMENT, '' AS COLUMN_KEY "
                                "FROM USER_TAB_COLUMNS WHERE TABLE_NAME='%s' ORDER BY COLUMN_ID"
                                % tn
                            )
                            if col_result.get("success") and col_result.get("data"):
                                cols_by_table[tn] = col_result["data"]
                        else:
                            # 未知类型 fallback: DESC table
                            col_result = db_agent.query("DESC %s" % tn)
                            if col_result.get("success") and col_result.get("data"):
                                cols = []
                                for c in col_result["data"]:
                                    field = c.get("Field", c.get("field", ""))
                                    key = c.get("Key", c.get("key", ""))
                                    cols.append(
                                        {
                                            "COLUMN_NAME": field,
                                            "COLUMN_TYPE": c.get(
                                                "Type", c.get("type", "")
                                            ),
                                            "DATA_TYPE": c.get(
                                                "Type", c.get("type", "")
                                            ),
                                            "COLUMN_COMMENT": c.get(
                                                "Comment", c.get("comment", "")
                                            )
                                            or "",
                                            "COLUMN_KEY": key.upper() if key else "",
                                            "ORDINAL_POSITION": len(cols),
                                        }
                                    )
                                cols_by_table[tn] = cols
                                pks = [
                                    c["COLUMN_NAME"]
                                    for c in cols
                                    if c.get("COLUMN_KEY") == "PRI"
                                ]
                                if pks:
                                    pks_by_table[tn] = pks


        return cols_by_table, pks_by_table
