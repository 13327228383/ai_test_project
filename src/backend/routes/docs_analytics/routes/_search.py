# -*- coding: utf-8 -*-
"""
routes/_search.py — 文件搜索路由
=================================
- api_docs_search — 文件搜索 API

★ 全 MySQL：在 source_documents / requirements / test_cases / run_logs
  四张表的内容字段上搜索（文件名 + 内容片段），无磁盘扫描。
"""

import logging
from flask import jsonify, request

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from src.backend.routes.docs_analytics.docs_analytics import (
    _SEARCH_MIN_QUERY_LEN,
    _SEARCH_DEFAULT_LIMIT,
)

_log = logging.getLogger(__name__)

# 搜索目标表配置：(表名, name 字段, 内容字段, doc_type 展示)
_SEARCH_TABLES = [
    ("source_documents", "doc_type", "content", "原始数据"),
    ("requirements", "req_id", "description", "过程产物"),
    ("test_cases", "case_id", "title", "过程产物"),
    ("run_logs", "run_id", "message", "测试结果"),
]


def register(bp):
    """注册文件搜索路由到 Blueprint。"""

    @bp.route("/api/docs/search")
    def api_docs_search():
        """搜索项目文档（文件名 + 内容）。?q=关键词&type=md|json|xlsx|all"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        q = request.args.get("q", "").strip()
        if not q or len(q) < _SEARCH_MIN_QUERY_LEN:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "搜索关键词至少 %d 个字符" % _SEARCH_MIN_QUERY_LEN,
                    }
                ),
                400,
            )
        file_type = request.args.get("type", "all").strip()
        max_results = request.args.get("limit", _SEARCH_DEFAULT_LIMIT, type=int)
        max_results = max(1, min(max_results, 500))

        results = []
        q_low = q.lower()
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            for table, name_col, content_col, dir_label in _SEARCH_TABLES:
                if len(results) >= max_results:
                    break
                try:
                    # 内容 LIKE 搜索（前 500 行，避免全表扫）
                    sql = (
                        "SELECT %s AS name, %s AS content FROM %s "
                        "WHERE project=%%s AND %s LIKE CONCAT('%%%%', %%s, '%%%%') LIMIT 200"
                    ) % (name_col, content_col, table, content_col)
                    rows = conn.execute(sql, (proj, q)).fetchall()
                except Exception as e:
                    _log.debug("[docs/search] 表 %s 搜索失败: %s", table, e)
                    continue
                for r in rows or []:
                    if len(results) >= max_results:
                        break
                    name = str(r.get("name", "") or "")
                    content = str(r.get("content", "") or "")
                    name_match = q_low in name.lower()
                    content_match = None
                    if content:
                        idx = content.lower().find(q_low)
                        if idx >= 0:
                            start = max(0, idx - 40)
                            end = min(len(content), idx + len(q) + 60)
                            snippet = content[start:end].replace("\n", " ")
                            if start > 0:
                                snippet = "..." + snippet
                            if end < len(content):
                                snippet += "..."
                            content_match = snippet
                    if not (name_match or content_match):
                        continue
                    ext = ".md"
                    if table == "source_documents":
                        from src.backend.routes.docs_analytics.fetchers.mysql_doc import (
                            _ext_for_doc_type,
                        )

                        ext = _ext_for_doc_type(name)
                    elif table == "test_cases":
                        ext = ".xlsx"
                    elif table == "run_logs":
                        ext = ".log"
                    # type 过滤
                    if file_type != "all" and ext.lstrip(".") != file_type:
                        continue
                    results.append(
                        {
                            "name": (
                                name + ext
                                if table != "source_documents"
                                else "%s%s" % (name, ext)
                            ),
                            "path": (
                                "mysql://%s/%s" % (table, name)
                                if table
                                in ("source_documents", "screenshots", "run_logs")
                                else "mysql://%s" % table
                            ),
                            "dir": dir_label,
                            "ext": ext,
                            "size": len(content),
                            "mtime": "",
                            "name_match": name_match,
                            "snippet": content_match or "",
                        }
                    )
        except Exception as e:
            _log.warning("[docs/search] 搜索失败: %s", e)
        finally:
            release_connection(conn)

        return jsonify(
            {
                "query": q,
                "results": results,
                "total": len(results),
                "truncated": len(results) >= max_results,
            }
        )
