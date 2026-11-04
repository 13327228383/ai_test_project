# -*- coding: utf-8 -*-

"""

追溯链路引擎 — 数据源提取

========================

从 YApi 规范、DB schema 文本、导航地图中提取结构化数据源列表。

"""


import re
from src.core.guarded import guard


import logging

_log = logging.getLogger(__name__)


class DataSourceExtractor:
    """数据源提取辅助类。"""

    @staticmethod
    def extract_api_list(yapi_specs: dict) -> list[dict]:
        """从 YApi 规范中提取 API 列表。

        支持格式：
        1. 结构化 JSON: {"data": [{"method":"GET","path":"/api/..."}]}
        2. Markdown 原始文本: {"raw_text": "# API文档\\n## 1. 登录\\n- URL: POST /api/login"}
        """

        endpoints = []

        if not yapi_specs:

            return endpoints

        # 处理 YApi 返回的多种格式

        data = yapi_specs.get("data", yapi_specs)

        if isinstance(data, list):

            items = data

        elif isinstance(data, dict):

            items = data.get("list", data.get("apis", data.get("interfaces", [])))
            # ★ 兜底：raw_text 格式（Markdown 文本）→ 正则提取 API 路径
            if not items and "raw_text" in data:
                raw = data["raw_text"]
                items = DataSourceExtractor._parse_markdown_endpoints(raw)

        else:

            return endpoints

        endpoints = [
            {
                "method": str(item.get("method", "")).upper(),
                "path": item.get("path", ""),
                "summary": item.get("title", item.get("name", "")),
            }
            for item in items
            if isinstance(item, dict)
        ]

        return endpoints

    @staticmethod
    def _parse_markdown_endpoints(md_text: str) -> list[dict]:
        """从 Markdown API 文档中提取 API 端点列表。
        匹配格式: - URL: METHOD /api/path  (或 URL：中文冒号)"""
        endpoints = []
        if not md_text:
            return endpoints
        import re

        # 匹配: "- URL: POST /api/login" 或 "- URL：POST /api/login"
        pattern = re.compile(r"[-*]\s*URL\s*[:：]\s*(\w+)\s+(/\S+)", re.IGNORECASE)
        seen = set()
        for m in pattern.finditer(md_text):
            method = m.group(1).upper()
            path = m.group(2)
            key = f"{method}:{path}"
            if key not in seen:
                seen.add(key)
                endpoints.append({"method": method, "path": path, "title": ""})
        return endpoints

    @staticmethod
    def parse_db_tables(db_schema: str) -> list[dict]:
        """从 DB schema 文本中解析表列表。

        支持格式：
        1. 列表格式: - TABLE_NAME: col1(type), col2(type), ...
        2. Markdown 表格: ## users 表\\n| 字段 | 类型 | 说明 |\\n| id | varchar(64) | ... |"""

        tables = []

        if not db_schema:

            return tables

        # 模式1：- TABLE_NAME: col1(type), col2(type), ...
        table_pattern = re.compile(r"[-*]\s*(\w+)\s*[:：]\s*(.+)", re.MULTILINE)

        for m in table_pattern.finditer(db_schema):

            tname = m.group(1).strip()

            cols_str = m.group(2).strip()

            cols = re.findall(r"(\w+)\s*\(", cols_str)

            if tname and not tname.startswith("_"):

                tables.append({"name": tname, "columns": cols[:10]})

        # 模式2：Markdown 表格（## table_name 表 后跟 | Field | Type | ... |）
        if not tables:
            tables = DataSourceExtractor._parse_markdown_tables(db_schema)

        return tables

    @staticmethod
    def _parse_markdown_tables(md_text: str) -> list[dict]:
        """从 Markdown 表格格式的 DB schema 中提取表名和列名。
        格式: ## table_name 表 → | 字段 | 类型 | 说明 | → | col1 | type | ... |"""
        tables = []
        if not md_text:
            return tables
        # 匹配 ## table_name 表 或 ## table_name
        heading_pattern = re.compile(r"^##\s+(\w+)\s*表?", re.MULTILINE)
        # 匹配表格行: | col_name | type | ... |
        row_pattern = re.compile(r"^\|\s*(\w+)\s*\|")

        lines = md_text.split("\n")
        current_table = None
        in_table = False
        header_seen = False

        for line in lines:
            line = line.strip()
            heading_match = heading_pattern.match(line)
            if heading_match:
                # 开始新表
                if current_table and current_table["columns"]:
                    tables.append(current_table)
                tname = heading_match.group(1)
                if not tname.startswith("_"):
                    current_table = {"name": tname, "columns": []}
                    in_table = False
                    header_seen = False
                else:
                    current_table = None
                continue

            if current_table is None:
                continue

            # 检测表格分隔行 |---|---|
            if re.match(r"^\|[\s\-|]+$", line):
                header_seen = True
                continue

            # 提取表格行
            row_match = row_pattern.match(line)
            if row_match and header_seen:
                col_name = row_match.group(1)
                # 过滤掉中文表头行 (如 | 字段 | 类型 |)
                if col_name not in (
                    "字段",
                    "Field",
                    "列名",
                    "Column",
                    "类型",
                    "Type",
                    "说明",
                    "Description",
                    "备注",
                ):
                    if col_name not in current_table["columns"]:
                        current_table["columns"].append(col_name)

        # 保存最后一个表
        if current_table and current_table["columns"]:
            tables.append(current_table)

        return tables

    @staticmethod
    def extract_page_list() -> list[dict]:
        """从 figma_pages 表提取页面列表（★ 2026-06-30: 替代 nav_map）。"""

        pages = []

        with guard("extract_page_list: from src.core.config_loader import c", log=_log, level=logging.WARNING):

            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""

            version_label = str(getattr(config, "project_version", "") or "")

            if not project or not version_label:

                return pages

            from src.core.db_manager import get_connection

            conn = get_connection()

            try:

                rows = conn.execute(
                    "SELECT name, canvas, page_type FROM figma_pages "
                    "WHERE project=%s AND version_label=%s ORDER BY canvas, name",
                    (project, version_label),
                ).fetchall()

                pages = [
                    {
                        "name": r["name"],
                        "module": r.get("canvas", ""),
                        "navigation_path": [r.get("canvas", ""), r["name"]],
                    }
                    for r in (rows or [])
                ]

            finally:

                with guard("extract_page_list: conn.close()", log=_log):
                    conn.close()



        return pages

    @staticmethod
    def match_api_in_steps(case_actions_json, all_endpoints: dict) -> set:
        """在步骤文本中匹配 API endpoint 引用。"""

        matched = set()

        text = str(case_actions_json)

        min_len = 4  # API_PATH_MIN_LEN

        for key in all_endpoints:

            ep = all_endpoints[key]

            path = ep.get("path", "")

            # 检查路径是否在步骤文本中被引用（忽略过短路径以减少误报）

            if path and len(path) > min_len and path in text:

                matched.add(key)

        return matched

    @staticmethod
    def match_tables_in_steps(case_actions_json, all_tables: dict) -> set:
        """在步骤文本中匹配数据库表名引用。"""

        matched = set()

        text = str(case_actions_json).upper()

        for table in all_tables:

            if table.upper() in text:

                matched.add(table)

        return matched

    # ══════════════════════════════════════════════════════════════════
    # ★ 2026-09-15 NEW: 从靶子系统代码自动扫描 API / DB / Pages
    # 根因：YApi / db_schema / figma 是外部数据源，靶子系统（Flask/SQLite）
    # 根本没有这些外部文档 → trace_links 只有 pages，api/db 全空。
    # 解决方案：自动从靶子源码正则扫描。
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def scan_target_code(target_dir: str) -> dict:
        """扫描靶子系统源码，提取 API 路由 + DB 表 + Pages。

        Args:
            target_dir: 靶子系统根目录（examples/smartshop, examples/api_demo ...）

        Returns:
            {"api_list": [...], "table_list": [...], "page_list": [...]}
        """
        from pathlib import Path

        result = {"api_list": [], "table_list": [], "page_list": []}
        if not target_dir or not Path(target_dir).exists():
            _log.debug("[scan_target_code] target_dir 不存在: %s", target_dir)
            return result

        target_path = Path(target_dir)

        # 扫描所有 .py 文件
        py_files = list(target_path.rglob("*.py"))
        if not py_files:
            _log.debug("[scan_target_code] 无 .py 文件: %s", target_dir)
            return result

        all_code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in py_files)

        # ── Flask / FastAPI 路由扫描 ──
        routes = []
        # Flask: @app.route("/path", methods=["GET","POST"])
        flask_pattern = re.compile(
            r"""@\w+\.route\s*\(\s*['"]([^'"]+)['"]\s*
                (?:,\s*methods\s*=\s*\[([^\]]+)\])?""",
            re.VERBOSE,
        )
        for m in flask_pattern.finditer(all_code):
            path = m.group(1).strip()
            methods_raw = m.group(2) or '"GET"'
            methods = re.findall(r"['\"](\w+)['\"]", methods_raw)
            if not methods:
                methods = ["GET"]
            for method in methods:
                routes.append({
                    "method": method.upper(),
                    "path": path,
                    "source": "flask_route",
                })

        # FastAPI: @app.get("/path"), @app.post("/path") ...
        http_methods = ["get", "post", "put", "delete", "patch", "options", "head"]
        fastapi_pattern = re.compile(
            r"@\w+\.({})\s*\(\s*['\"]([^'\"]+)['\"]".format("|".join(http_methods))
        )
        for m in fastapi_pattern.finditer(all_code):
            method = m.group(1).upper()
            path = m.group(2).strip()
            routes.append({
                "method": method,
                "path": path,
                "source": "fastapi_route",
            })

        # 去重
        seen_ep = set()
        for r in routes:
            key = "{}:{}".format(r["method"], r["path"])
            if key not in seen_ep:
                seen_ep.add(key)
                result["api_list"].append({
                    "method": r["method"],
                    "path": r["path"],
                    "summary": "",
                })

        # ── DB 表扫描 ──
        tables = []
        # SQLite / 原生 SQL: CREATE TABLE [IF NOT EXISTS] table_name
        sqlite_pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?",
            re.IGNORECASE,
        )
        for m in sqlite_pattern.finditer(all_code):
            tname = m.group(1)
            # 过滤掉 IF NOT EXISTS 里的 IF
            if tname.upper() in ("IF", "NOT", "EXISTS"):
                continue
            if tname not in tables:
                tables.append(tname)

        # SQLAlchemy: class Xxx(db.Model) + __tablename__ = "xxx"
        sa_pattern = re.compile(
            r"""class\s+(\w+)\s*\(\s*(?:db|SQLAlchemy)\.Model\s*\).*?
                __tablename__\s*=\s*['"](\w+)['"]""",
            re.DOTALL | re.VERBOSE,
        )
        for m in sa_pattern.finditer(all_code):
            tname = m.group(2)
            if tname not in tables:
                tables.append(tname)

        for t in tables:
            result["table_list"].append({"name": t, "columns": []})

        # ── Pages 提取 ──
        # 从 Flask 路由里提取非 API 的页面路由（path 不含 /api/）
        page_routes = [
            r for r in routes
            if "/api/" not in r["path"].lower() and not r["path"].startswith("/api")
        ]
        seen_pages = set()
        for r in page_routes:
            # 把 Flask 变量占位符 <int:pid> 转成可读名
            clean_path = re.sub(r"<[^>]+>", "{param}", r["path"])
            if clean_path not in seen_pages:
                seen_pages.add(clean_path)
                result["page_list"].append({
                    "name": clean_path or "/",
                    "module": "Routes",
                    "navigation_path": ["Routes", clean_path or "/"],
                })

        _log.info(
            "[scan_target_code] ✅ %s → API=%d, DB_TABLES=%d, PAGES=%d",
            target_dir, len(result["api_list"]), len(result["table_list"]), len(result["page_list"]),
        )
        return result
