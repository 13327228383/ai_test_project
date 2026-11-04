# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
SessionDbMetaMixin - DB 元数据查询
==================================
负责：数据库连接管理、表列表查询、表结构摘要、外键发现、关系采样推断。
"""

import re
import logging
from collections import Counter

from src.core.config_loader import config

_log = logging.getLogger(__name__)


class SessionDbMetaMixin:
    """DB 元数据查询 Mixin。"""

    def _ensure_db_connected(self) -> bool:
        if self._db_connected:
            return True
        try:
            from src.extensions.db.db_agent import db_agent

            db_agent.connect()
            self._db_connected = True
            return True
        except Exception as e:
            _log.warning("_ensure_db_connected 连接失败: %s", e)
            return False

    def _get_table_list(self) -> list[str]:
        if self._db_schema_cache and "tables" in self._db_schema_cache:
            return self._db_schema_cache["tables"]
        if not self._ensure_db_connected():
            return []
        with guard("_get_table_list: from src.extensions.db.db_agent impo", log=_log, level=logging.WARNING):
            from src.extensions.db.db_agent import db_agent

            db_type = config.db_type.lower()
            if db_type == "yasdb":
                result = db_agent.query("SELECT TABLE_NAME FROM USER_TABLES")
            elif db_type == "oracle":
                result = db_agent.query("SELECT TABLE_NAME FROM USER_TABLES")
            elif db_type == "mysql":
                result = db_agent.query("SHOW TABLES")
            elif db_type == "postgresql":
                result = db_agent.query(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                    ("public",),
                )
            elif db_type == "sqlite":
                result = db_agent.query(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            else:
                return []
            if result.get("success") and result.get("data"):
                tables = [
                    str(list(row.values())[0]) if isinstance(row, dict) else str(row[0])
                    for row in result["data"]
                ]
                if self._db_schema_cache is None:
                    self._db_schema_cache = {}
                self._db_schema_cache["tables"] = tables
                return tables
        return []

    def _batch_fetch_columns(
        self, tables: list[str], include_extra: bool = False
    ) -> dict[str, list[tuple[str, ...]]]:
        """批量查询多张表的列元数据。

        返回: {原始表名: [(列名, 类型, ...), ...]}。
        对 MySQL/PostgreSQL/Oracle/yasdb 使用单条 IN 查询；对 sqlite 查询
        sqlite_master 并解析 CREATE TABLE 语句。查询失败时记录日志并返回空字典。
        """
        if not tables or not self._ensure_db_connected():
            return {}
        try:
            from src.extensions.db.db_agent import db_agent
            from src.core.config_loader import config

            db_type = config.db_type.lower()
            safe_names = [self._sanitize_table_name(t) for t in tables]
            if not safe_names:
                return {}
            # 保留原始表名大小写（DB 元数据可能返回全大写/小写）
            orig_by_lower = {t.lower(): t for t in tables}
            columns_by_table: dict[str, list[tuple[str, ...]]] = {}

            if db_type in ("yasdb", "oracle"):
                in_clause = ",".join("'%s'" % n for n in safe_names)
                sql = (
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE "
                    "FROM USER_TAB_COLUMNS "
                    "WHERE TABLE_NAME IN (%s) "
                    "ORDER BY TABLE_NAME, COLUMN_ID" % in_clause
                )
                result = db_agent.query(sql)
            elif db_type == "mysql":
                in_clause = ",".join("'%s'" % n for n in safe_names)
                sql = (
                    "SELECT table_name, column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name IN (%s) "
                    "ORDER BY table_name, ordinal_position"
                )
                result = db_agent.query(sql, (config.db_name,))
            elif db_type == "postgresql":
                in_clause = ",".join("'%s'" % n for n in safe_names)
                sql = (
                    "SELECT table_name, column_name, data_type, is_nullable, character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_name IN (%s) AND table_schema=%s "
                    "ORDER BY table_name, ordinal_position"
                )
                result = db_agent.query(sql, ("public",))
            elif db_type == "sqlite":
                in_clause = ",".join("'%s'" % n for n in safe_names)
                sql = (
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='table' AND name IN (%s)" % in_clause
                )
                result = db_agent.query(sql)
                return self._parse_sqlite_columns_result(result, tables)
            else:
                return {}

            if result.get("success") and result.get("data"):
                for row in result["data"]:
                    vals = list(row.values()) if isinstance(row, dict) else row
                    if len(vals) < 3:
                        continue
                    t_name = orig_by_lower.get(str(vals[0]).lower(), str(vals[0]))
                    col_name = str(vals[1])
                    col_type = str(vals[2])
                    extra = tuple(str(v) for v in vals[3:]) if include_extra else ()
                    columns_by_table.setdefault(t_name, []).append(
                        (col_name, col_type) + extra
                    )
            return columns_by_table
        except Exception as e:
            _log.warning("_batch_fetch_columns 失败: %s", e)
            return {}

    @staticmethod
    def _parse_sqlite_columns_result(
        result: dict, tables: list[str]
    ) -> dict[str, list[tuple[str, str]]]:
        """解析 sqlite_master 批量查询结果，返回 {原始表名: [(列名, 类型), ...]}。"""
        columns_by_table: dict[str, list[tuple[str, str]]] = {}
        if not result.get("success") or not result.get("data"):
            return columns_by_table
        exact_lookup = {t: t for t in tables}
        lower_lookup = {t.lower(): t for t in tables}
        for row in result["data"]:
            vals = list(row.values()) if isinstance(row, dict) else row
            if len(vals) < 2:
                continue
            raw_name = str(vals[0])
            sql_text = str(vals[1]) if vals[1] else ""
            t_name = exact_lookup.get(
                raw_name, lower_lookup.get(raw_name.lower(), raw_name)
            )
            cols = SessionDbMetaMixin._parse_sqlite_create_table(sql_text)
            if cols:
                columns_by_table[t_name] = cols
        return columns_by_table

    @staticmethod
    def _parse_sqlite_create_table(sql: str) -> list[tuple[str, str]]:
        """从 SQLite CREATE TABLE 语句中提取 (column_name, data_type) 列表。"""
        if not sql:
            return []
        m = re.search(r"\((.*)\)\s*$", sql, re.DOTALL | re.IGNORECASE)
        if not m:
            return []
        body = m.group(1)
        parts = []
        depth = 0
        current: list[str] = []
        for ch in body:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))

        cols: list[tuple[str, str]] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if re.match(
                r"^(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b",
                part,
                re.IGNORECASE,
            ):
                continue
            tokens = part.split()
            if not tokens:
                continue
            col_name = tokens[0].strip("\"'`[]")
            col_type = tokens[1] if len(tokens) > 1 else ""
            cols.append((col_name, col_type))
        return cols

    def _get_table_schema_summary(self) -> str:
        if self._db_schema_cache and "schema_text" in self._db_schema_cache:
            return self._db_schema_cache["schema_text"]
        if not self._ensure_db_connected():
            return ""
        try:
            lines = []
            tables = self._get_table_list()
            if not tables:
                return ""
            sample_tables = tables[: self.SCHEMA_SAMPLE_TABLES]
            columns_by_table = self._batch_fetch_columns(
                sample_tables, include_extra=False
            )
            for table in sample_tables:
                cols = columns_by_table.get(table, [])
                if not cols:
                    continue
                col_strs = [
                    "%s(%s)" % (col[0], col[1])
                    for col in cols[: self.COLS_PER_TABLE_MAX]
                ]
                lines.append("- %s: %s" % (table, ", ".join(col_strs)))
            result_text = "\n".join(lines)
            if self._db_schema_cache is None:
                self._db_schema_cache = {}
            self._db_schema_cache["schema_text"] = result_text
            return result_text
        except Exception as e:
            _log.warning("_get_table_schema_summary 失败: %s", e)
            return ""

    def _get_real_foreign_keys(self) -> list[dict[str, str]]:
        """从数据库元数据查询真实外键约束。"""
        fks = []
        with guard("_get_real_foreign_keys: from src.extensions.db.db_agent impo", log=_log, level=logging.WARNING):
            from src.extensions.db.db_agent import db_agent

            db_type = config.db_type.lower()
            if db_type in ("yasdb", "oracle"):
                result = db_agent.query(
                    "SELECT a.TABLE_NAME, a.COLUMN_NAME, "
                    "(SELECT TABLE_NAME FROM USER_CONSTRAINTS "
                    " WHERE CONSTRAINT_NAME = c.R_CONSTRAINT_NAME) AS REF_TABLE "
                    "FROM USER_CONS_COLUMNS a "
                    "JOIN USER_CONSTRAINTS c ON a.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
                    "WHERE c.CONSTRAINT_TYPE = 'R'"
                )
            elif db_type == "mysql":
                result = db_agent.query(
                    "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                    "FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
                    (config.db_name,),
                )
            elif db_type == "postgresql":
                result = db_agent.query(
                    "SELECT tc.table_name, kcu.column_name, "
                    "ccu.table_name AS referenced_table_name, ccu.column_name AS referenced_column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name "
                    "WHERE tc.constraint_type = 'FOREIGN KEY'"
                )
            elif db_type == "sqlite":
                result = db_agent.query(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES%'"
                )
                if result.get("success") and result.get("data"):
                    for row in result["data"]:
                        vals = list(row.values()) if isinstance(row, dict) else row
                        sql_text = str(vals[0]) if vals else ""
                        for m in re.finditer(
                            r"(\w+)\s*.*?REFERENCES\s+(\w+)\s*\((\w+)\)",
                            sql_text,
                            re.IGNORECASE,
                        ):
                            fks.append(
                                {
                                    "table": "?",
                                    "column": m.group(1),
                                    "ref_table": m.group(2),
                                    "ref_column": m.group(3),
                                }
                            )
                    return fks
            else:
                return fks
            if result.get("success") and result.get("data"):
                for row in result["data"]:
                    vals = list(row.values()) if isinstance(row, dict) else row
                    if len(vals) >= 4:
                        fks.append(
                            {
                                "table": str(vals[0]),
                                "column": str(vals[1]),
                                "ref_table": str(vals[2]),
                                "ref_column": str(vals[3]),
                            }
                        )
                    elif len(vals) >= 3:
                        fks.append(
                            {
                                "table": str(vals[0]),
                                "column": str(vals[1]),
                                "ref_table": str(vals[2]),
                                "ref_column": "?",
                            }
                        )
        return fks

    @staticmethod
    def _guess_referenced_table(
        col_name: str, own_table: str, all_tables: list[str]
    ) -> str | None:
        """根据列名推断可能引用的目标表。"""
        cl = col_name.lower().strip()
        # 去掉常见后缀
        for suffix in ("_id", "_code", "_no", "_key", "id", "code"):
            if cl.endswith("_" + suffix):
                cl = cl[: -len("_" + suffix)]
                break
            elif cl == suffix:
                return None
        # 尝试匹配表名
        all_lower = [t.lower().strip() for t in all_tables]
        for t in all_tables:
            tl = t.lower().strip()
            if tl == own_table.lower().strip():
                continue
            if cl == tl or cl in tl or tl in cl:
                return t
        # 尝试去掉前缀 SYS_ / T_ / TB_ 后匹配
        for prefix in ("sys_", "t_", "tb_", "tbl_"):
            if cl.startswith(prefix):
                base = cl[len(prefix) :]
                for t in all_tables:
                    tl = t.lower().strip()
                    if tl == own_table.lower().strip():
                        continue
                    if base in tl or tl in base:
                        return t
        return None

    @staticmethod
    def _format_relationship_lines(
        real_fks: list[dict[str, str]],
        discovered: dict[str, list[tuple]],
        max_forward: int | None = None,
        edge_type_filter: str | None = None,
    ) -> list[str]:
        """格式化真实外键与采样推断关系，正确标注 1:N / N:1 / N:M 基数。"""
        all_edges: list[tuple[str, str, str, str, str, dict]] = []
        for fk in real_fks:
            all_edges.append(
                (
                    fk.get("table", ""),
                    fk.get("column", ""),
                    fk.get("ref_table", ""),
                    fk.get("ref_column", ""),
                    "fk",
                    {},
                )
            )
        for table, cols in discovered.items():
            for col_name, ref_table, top_count, total in cols:
                all_edges.append(
                    (
                        table,
                        col_name,
                        ref_table,
                        "?",
                        "inferred",
                        {
                            "top_count": top_count,
                            "total": total,
                        },
                    )
                )

        pair_set = set((a, b) for a, _, b, _, _, _ in all_edges if a and b)

        lines: list[str] = []
        forward_count = 0
        for a, a_col, b, b_col, edge_type, info in all_edges:
            if edge_type_filter and edge_type != edge_type_filter:
                continue
            if edge_type == "fk" and max_forward is not None:
                forward_count += 1
                if forward_count > max_forward:
                    break

            reverse_exists = (b, a) in pair_set
            if edge_type == "fk":
                if reverse_exists:
                    lines.append("- `%s.%s` → `%s.%s` （N:M）" % (a, a_col, b, b_col))
                else:
                    lines.append("- `%s.%s` → `%s.%s` （N:1）" % (a, a_col, b, b_col))
                    lines.append("- `%s.%s` → `%s.%s` （1:N）" % (b, b_col, a, a_col))
            else:
                top_count = info["top_count"]
                total = info["total"]
                pct = top_count * 100 // total if total else 0
                if reverse_exists:
                    lines.append(
                        "- `%s.%s` → `%s`（N:M，%d/%d=%d%% 相同值，推断）"
                        % (a, a_col, b, top_count, total, pct)
                    )
                else:
                    lines.append(
                        "- `%s.%s` → `%s`（N:1，%d/%d=%d%% 相同值，推断）"
                        % (a, a_col, b, top_count, total, pct)
                    )
                    lines.append("- `%s` → `%s.%s`（1:N，推断）" % (b, a, a_col))
        return lines

    def _discover_relationships_by_sampling(self) -> str:
        """智能发现表间关系：真实 FK + 采样推断。
        对每列取前 50 行排序数据，若 ≥20 行值相同 → 可能为关联字段。"""
        if self._db_schema_cache and "relationships" in self._db_schema_cache:
            return self._db_schema_cache["relationships"]
        if not self._ensure_db_connected():
            return ""

        try:
            from src.extensions.db.db_agent import db_agent

            db_type = config.db_type.lower()
            tables = self._get_table_list()
            if not tables:
                return ""

            if db_type == "yasdb":
                limit_clause = (
                    "FETCH FIRST %d ROWS ONLY" % self.RELATIONSHIP_SAMPLE_ROWS
                )
                q_l, q_r = '"', '"'
            elif db_type == "oracle":
                limit_clause = (
                    "FETCH FIRST %d ROWS ONLY" % self.RELATIONSHIP_SAMPLE_ROWS
                )
                q_l, q_r = '"', '"'
            elif db_type == "postgresql":
                limit_clause = "LIMIT %d" % self.RELATIONSHIP_SAMPLE_ROWS
                q_l, q_r = '"', '"'
            elif db_type == "sqlite":
                limit_clause = "LIMIT %d" % self.RELATIONSHIP_SAMPLE_ROWS
                q_l, q_r = '"', '"'
            else:
                limit_clause = "LIMIT %d" % self.RELATIONSHIP_SAMPLE_ROWS
                q_l, q_r = "`", "`"

            # Step 1: 真实外键约束
            real_fks = self._get_real_foreign_keys()

            # Step 2: 采样推断 — 对前 N 张表的非大字段列做采样
            discovered = {}
            skip_patterns = (
                "content",
                "text",
                "desc",
                "remark",
                "note",
                "image",
                "url",
                "path",
                "addr",
                "address",
                "phone",
                "email",
                "password",
                "pwd",
                "salt",
            )

            scan_tables = tables[: self.RELATIONSHIP_SCAN_TABLES]
            columns_by_table = self._batch_fetch_columns(
                scan_tables, include_extra=False
            )

            for table in scan_tables:
                try:
                    tname = self._sanitize_table_name(table)
                    col_rows = columns_by_table.get(table, [])
                    if not col_rows:
                        continue

                    for col_info in col_rows:
                        col_name = str(col_info[0]) if col_info else ""
                        col_lower = col_name.lower().strip()
                        if any(p in col_lower for p in skip_patterns):
                            continue

                        try:
                            cname = self._sanitize_table_name(col_name)
                            sql = f"SELECT {q_l}{cname}{q_r} FROM {q_l}{tname}{q_r} ORDER BY {q_l}{cname}{q_r} {limit_clause}"
                            sample = db_agent.query(sql)
                            if not sample.get("success") or not sample.get("data"):
                                continue
                            rows = sample["data"]
                            if len(rows) < self.MIN_SAMPLE_ROWS:
                                continue

                            values = []
                            for r in rows:
                                rv = list(r.values()) if isinstance(r, dict) else r
                                if rv and rv[0] is not None:
                                    values.append(str(rv[0]))
                            if not values:
                                continue

                            counter = Counter(values)
                            top_val, top_count = counter.most_common(1)[0]
                            # 判断：采样行中 ≥FK_MATCH_THRESHOLD 行相同 且 唯一值 ≤ENUM_UNIQUE_MAX 个 → 枚举型关联字段
                            if (
                                top_count >= self.FK_MATCH_THRESHOLD
                                and len(counter) <= self.ENUM_UNIQUE_MAX
                            ):
                                ref_table = self._guess_referenced_table(
                                    col_name, table, tables
                                )
                                if ref_table:
                                    if table not in discovered:
                                        discovered[table] = []
                                    discovered[table].append(
                                        (col_name, ref_table, top_count, len(rows))
                                    )
                        except Exception as col_err:
                            _log.debug(
                                "列 %s.%s 采样跳过: %s", table, col_name, col_err
                            )
                            continue
                except Exception as tbl_err:
                    _log.debug("表 %s 采样跳过: %s", table, tbl_err)
                    continue

            # Step 3: 格式化输出
            lines = ["### 表间关系（智能分析）", ""]
            has_content = False

            fk_lines = self._format_relationship_lines(
                real_fks,
                discovered,
                max_forward=self.FK_DISPLAY_MAX,
                edge_type_filter="fk",
            )
            if fk_lines:
                has_content = True
                lines.append("**数据库定义的外键约束：**")
                lines.extend(fk_lines)
                lines.append("")

            inferred_lines = self._format_relationship_lines(
                real_fks, discovered, edge_type_filter="inferred"
            )
            if inferred_lines:
                has_content = True
                lines.append(
                    "**采样推断的关联字段**（前50行中≥20行相同值，疑似外键）："
                )
                lines.extend(inferred_lines)
                lines.append("")

            if not has_content:
                lines.append("（未发现明确的表间关联关系）")
                lines.append("")

            lines.append(
                "★ 测试用例设计提示：关联字段在生成测试数据时需引用对应表的主键值，"
                "删除操作需考虑外键约束顺序。"
            )

            result = "\n".join(lines)
            if self._db_schema_cache is None:
                self._db_schema_cache = {}
            self._db_schema_cache["relationships"] = result
            return result
        except Exception as e:
            _log.warning("_discover_relationships_by_sampling 失败: %s", e)
            return ""
