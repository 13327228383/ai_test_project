# -*- coding: utf-8 -*-
"""
routes/_file_tree.py — 文件浏览类路由
======================================

- api_docs_files     — MySQL 虚拟文件树 API（叶子 path 用 mysql:// URI，preview 据此直查 MySQL）
- api_docs_file_list — 子目录文件列表 API（MySQL 虚拟）
"""

import logging
from src.core.guarded import guard
from flask import jsonify, request

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from src.backend.routes.docs_analytics.fetchers.search import (
    _FILE_ICONS,
    _PREVIEWABLE,
    _EDITABLE,
)

_log = logging.getLogger(__name__)


def _doc_type_ext(dt: str) -> str:
    """source_documents.doc_type → 预览扩展名（决定前端图标 + preview 路由）。"""
    if dt in ("yapi", "db_schema"):
        return ".json"
    if dt in ("smoke_excel", "test_data_excel"):
        return ".xlsx"
    if dt == "xmind":
        return ".xmind"
    return ".md"  # confluence/requirement/report/figma/html_report


def register(bp):
    """注册文件浏览类路由到 Blueprint。"""

    @bp.route("/api/docs/files")
    def api_docs_files():
        """构建 MySQL 虚拟文件树供预览面板使用（取代扫 data/{project}/ 磁盘目录）。
        叶子节点 path 用 mysql:// URI，preview/save 据此直查 MySQL，无磁盘依赖。"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        from src.core.versioning import current_version_id, current_version_label

        active_vid = current_version_id(proj)
        active_vlabel = current_version_label(proj)
        tree = []
        with guard("api_docs_files: from src.core.db_manager import ( ge", log=_log, level=logging.WARNING):
            from src.core.db_manager import (
                get_connection as _gc,
                release_connection as _rc,
            )

            _conn = _gc()
            try:
                # ── 原始数据：source_documents 按 doc_type，每类一个文件叶子 ──
                # ★ 07-06 报告分表：report/coverage_report/html_report 已迁移到 generated_reports，
                #   source_documents 中残留的报告类 doc_type 不再在文档树展示
                raw_children = []
                _excluded_source_doc_types = {
                    "report",
                    "coverage_report",
                    "html_report",
                }
                # ★ 07-08: 查询 source_url 用于前端显示原始来源
                rows = _conn.execute(
                    "SELECT doc_type, COUNT(*) AS cnt, MAX(updated_at) AS ut, MAX(char_count) AS sz, "
                    "MAX(source_url) AS source_url "
                    "FROM source_documents WHERE project=%s AND version_label=%s GROUP BY doc_type ORDER BY doc_type",
                    (proj, active_vlabel),
                ).fetchall()
                for r in rows or []:
                    dt = r["doc_type"] or "unknown"
                    if dt in _excluded_source_doc_types:
                        continue
                    ext = _doc_type_ext(dt)
                    # ★ 07-08: 区分 yapi 和 yapi_structured 的用途说明
                    _dt_label = dt
                    if dt == "yapi":
                        _dt_label = "yapi (文本版·用于AI需求拆解)"
                    elif dt == "yapi_structured":
                        _dt_label = "yapi_structured (结构化版·用于数据工厂)"
                    raw_children.append(
                        {
                            "name": "%s%s (%d)" % (_dt_label, ext, r["cnt"]),
                            "path": "mysql://source_documents/%s" % dt,
                            "type": "file",
                            "ext": ext,
                            "size": int(r.get("sz") or 0),
                            "mtime": str(r["ut"])[:16] if r.get("ut") else "",
                            "icon": _FILE_ICONS.get(ext, "📎"),
                            "previewable": ext in _PREVIEWABLE,
                            "editable": ext in _EDITABLE,
                            "source_url": r.get("source_url") or "",
                        }
                    )
                if raw_children:
                    tree.append(
                        {
                            "name": "原始数据",
                            "path": "原始数据",
                            "type": "dir",
                            "children": raw_children,
                        }
                    )

                # ── 原始数据：Figma 设计稿（独立叶子，从 figma_pages 表查询）──
                # ★ fix: Figma 数据由 figma_page_store 写入 figma_pages 表，不在 source_documents
                with guard("api_docs_files: figma_row = _conn.execute( 'SELECT C", log=_log):
                    figma_row = _conn.execute(
                        "SELECT COUNT(*) AS cnt, MAX(updated_at) AS ut, MAX(element_count) AS elem "
                        "FROM figma_pages WHERE project=%s AND version_label=%s",
                        (proj, active_vlabel),
                    ).fetchone()
                    if figma_row and figma_row["cnt"]:
                        # 找到已有"原始数据"分区则追加，否则新建
                        figma_leaf = {
                            "name": "figma_design.md (%d 页, %d 元素)"
                            % (figma_row["cnt"], int(figma_row.get("elem") or 0)),
                            "path": "mysql://figma_pages",
                            "type": "file",
                            "ext": ".md",
                            "size": 0,
                            "mtime": (
                                str(figma_row["ut"])[:16] if figma_row.get("ut") else ""
                            ),
                            "icon": "🎨",
                            "previewable": True,
                            "editable": False,
                            "source_url": "",
                        }
                        if tree and tree[-1]["name"] == "原始数据":
                            tree[-1]["children"].append(figma_leaf)
                        else:
                            tree.append(
                                {
                                    "name": "原始数据",
                                    "path": "原始数据",
                                    "type": "dir",
                                    "children": [figma_leaf],
                                }
                            )

                # ── 过程产物：需求拆解 + 测试用例（各一个汇总叶子）──
                proc_children = []
                req_row = _conn.execute(
                    "SELECT COUNT(*) AS cnt, MAX(updated_at) AS ut FROM requirements WHERE project=%s AND version_id=%s",
                    (proj, active_vid),
                ).fetchone()
                if req_row and req_row["cnt"]:
                    proc_children.append(
                        {
                            "name": "需求拆解 (%d).md" % req_row["cnt"],
                            "path": "mysql://requirements",
                            "type": "file",
                            "ext": ".md",
                            "size": 0,
                            "mtime": (
                                str(req_row["ut"])[:16] if req_row.get("ut") else ""
                            ),
                            "icon": "📄",
                            "previewable": True,
                            "editable": False,
                        }
                    )
                case_row = _conn.execute(
                    "SELECT COUNT(*) AS cnt, MAX(updated_at) AS ut FROM test_cases WHERE project=%s AND version_id=%s",
                    (proj, active_vid),
                ).fetchone()
                if case_row and case_row["cnt"]:
                    proc_children.append(
                        {
                            "name": "测试用例 (%d).xlsx" % case_row["cnt"],
                            "path": "mysql://test_cases",
                            "type": "file",
                            "ext": ".xlsx",
                            "size": 0,
                            "mtime": (
                                str(case_row["ut"])[:16] if case_row.get("ut") else ""
                            ),
                            "icon": "📊",
                            "previewable": True,
                            "editable": False,
                        }
                    )
                if proc_children:
                    tree.append(
                        {
                            "name": "过程产物",
                            "path": "过程产物",
                            "type": "dir",
                            "children": proc_children,
                        }
                    )

                # ── 测试结果：生成报告（report / html_report / coverage_report）+ 执行截图 + 执行日志 ──
                test_children = []
                _gen_report_meta = {
                    "report": ("Markdown 测试报告", ".md", "📄"),
                    "html_report": ("HTML 测试报告", ".html", "🌐"),
                    "coverage_report": ("覆盖率报告", ".md", "📊"),
                }
                # ★ 性能修复：合并 3 次循环查询为 1 次 GROUP BY，减少 2/3 的 DB 往返
                gr_rows = _conn.execute(
                    "SELECT report_type, COUNT(*) AS cnt, MAX(updated_at) AS ut, MAX(char_count) AS sz "
                    "FROM generated_reports WHERE project=%s AND version_id=%s GROUP BY report_type",
                    (proj, active_vid),
                ).fetchall()
                for gr_row in gr_rows or []:
                    report_type = gr_row["report_type"]
                    if report_type not in _gen_report_meta:
                        continue
                    if not (gr_row and gr_row["cnt"]):
                        continue
                    label, ext, icon = _gen_report_meta[report_type]
                    test_children.append(
                        {
                            "name": "%s (%d)" % (label, gr_row["cnt"]),
                            "path": "mysql://generated_reports/%s" % report_type,
                            "type": "file",
                            "ext": ext,
                            "size": int(gr_row.get("sz") or 0),
                            "mtime": (
                                str(gr_row.get("ut"))[:16] if gr_row.get("ut") else ""
                            ),
                            "icon": icon,
                            "previewable": ext in _PREVIEWABLE,
                            "editable": False,
                        }
                    )

                if test_children:
                    tree.append(
                        {
                            "name": "测试结果",
                            "path": "测试结果",
                            "type": "dir",
                            "children": test_children,
                        }
                    )

                # ── 执行截图：独立一级分区 ──
                ss_rows = _conn.execute(
                    "SELECT case_id, COUNT(*) AS cnt, MAX(created_at) AS ut "
                    "FROM execution_screenshots WHERE project=%s AND version_id=%s GROUP BY case_id ORDER BY case_id LIMIT 200",
                    (proj, active_vid),
                ).fetchall()
                if ss_rows:
                    ss_leaves = [
                        {
                            "name": "%s.png (%d)" % (r["case_id"], r["cnt"]),
                            "path": "mysql://screenshots/%s" % r["case_id"],
                            "type": "file",
                            "ext": ".png",
                            "size": 0,
                            "mtime": str(r["ut"])[:16] if r.get("ut") else "",
                            "icon": "🖼️",
                            "previewable": True,
                            "editable": False,
                            "deletable": True,
                        }
                        for r in ss_rows
                    ]
                    tree.append(
                        {
                            "name": "执行截图",
                            "path": "执行截图",
                            "type": "dir",
                            "children": ss_leaves,
                        }
                    )

                # ── 执行日志：独立一级分区 ──
                run_rows = _conn.execute(
                    "SELECT run_id, COUNT(*) AS cnt, MAX(created_at) AS ut "
                    "FROM run_logs WHERE project=%s AND version_id=%s GROUP BY run_id ORDER BY MAX(created_at) DESC LIMIT 200",
                    (proj, active_vid),
                ).fetchall()
                if run_rows:
                    run_leaves = [
                        {
                            "name": "%s.log (%d行)" % (r["run_id"], r["cnt"]),
                            "path": "mysql://run_logs/%s" % r["run_id"],
                            "type": "file",
                            "ext": ".log",
                            "size": 0,
                            "mtime": str(r["ut"])[:16] if r.get("ut") else "",
                            "icon": "📝",
                            "previewable": True,
                            "editable": False,
                            "deletable": True,
                        }
                        for r in run_rows
                    ]
                    tree.append(
                        {
                            "name": "执行日志",
                            "path": "执行日志",
                            "type": "dir",
                            "children": run_leaves,
                        }
                    )
            finally:
                _rc(_conn)
        return jsonify({"tree": tree, "project": proj})

    @bp.route("/api/docs/file-list")
    def api_docs_file_list():
        """返回指定子目录下的文件列表（MySQL 虚拟，取代扫磁盘）。?dir=过程产物/测试用例"""
        ok, err = check_admin_or_permission("docs.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        from src.core.versioning import current_version_id, current_version_label

        active_vid = current_version_id(proj)
        active_vlabel = current_version_label(proj)
        subdir = request.args.get("dir", "").strip()
        if not subdir:
            return jsonify({"ok": False, "error": "缺少 dir 参数"}), 400
        files = []
        with guard("api_docs_file_list: from src.core.db_manager import ( ge", log=_log, level=logging.WARNING):
            from src.core.db_manager import (
                get_connection as _gc,
                release_connection as _rc,
            )

            _conn = _gc()
            try:
                if subdir == "过程产物/需求拆解":
                    rows = _conn.execute(
                        "SELECT req_id, module FROM requirements WHERE project=%s AND version_id=%s ORDER BY req_id LIMIT 500",
                        (proj, active_vid),
                    ).fetchall()
                    files = [
                        "%s_%s.xlsx" % (r["req_id"], (r["module"] or "")[:10])
                        for r in rows or []
                    ]
                elif subdir == "过程产物/测试用例":
                    rows = _conn.execute(
                        "SELECT case_id, title FROM test_cases WHERE project=%s AND version_id=%s ORDER BY case_id LIMIT 500",
                        (proj, active_vid),
                    ).fetchall()
                    files = [
                        "%s_%s.xlsx" % (r["case_id"], (r["title"] or "")[:10])
                        for r in rows or []
                    ]
                elif subdir.startswith("原始数据/"):
                    dt = subdir.split("/", 1)[1]
                    # ★ source_documents 无 id 字段，PK 为 (project,doc_type,version_label)
                    rows = _conn.execute(
                        "SELECT version_label, updated_at FROM source_documents WHERE project=%s AND doc_type=%s AND version_label=%s ORDER BY updated_at DESC LIMIT 500",
                        (proj, dt, active_vlabel),
                    ).fetchall()
                    files = [
                        "%s_%s.md" % (dt, str(r["updated_at"])[:10]) for r in rows or []
                    ]
                elif subdir == "执行截图":
                    rows = _conn.execute(
                        "SELECT case_id, COUNT(*) AS cnt FROM execution_screenshots WHERE project=%s AND version_id=%s GROUP BY case_id ORDER BY case_id LIMIT 500",
                        (proj, active_vid),
                    ).fetchall()
                    files = [
                        "fail_%s.png (%d)" % (r["case_id"], r["cnt"])
                        for r in rows or []
                    ]
                elif subdir == "执行日志":
                    rows = _conn.execute(
                        "SELECT DISTINCT run_id FROM run_logs WHERE project=%s AND version_id=%s ORDER BY MAX(created_at) DESC LIMIT 500",
                        (proj, active_vid),
                    ).fetchall()
                    files = [str(r["run_id"]) + ".log" for r in rows or []]
            finally:
                _rc(_conn)
        return jsonify({"files": files, "dir": subdir, "count": len(files)})

    @bp.route("/api/docs/delete", methods=["POST"])
    def api_docs_delete():
        """删除指定 MySQL 虚拟文件。

        支持 path:
          - mysql://source_documents/{doc_type}
          - mysql://screenshots/{case_id}
          - mysql://run_logs/{run_id}
        需要管理员权限。
        """
        ok, err = check_admin_or_permission("admin", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        path = request.json.get("path", "").strip() if request.json else ""
        if not path:
            return jsonify({"ok": False, "error": "缺少 path 参数"}), 400

        try:
            from src.core.project_purge import purge_specs
            from src.core.versioning import current_version_label

            res = purge_specs(proj, [path], version_label=current_version_label(proj))
            if res["errors"]:
                return (
                    jsonify({"ok": False, "error": "不支持的删除目标: " + path}),
                    400,
                )
        except Exception as e:
            _log.warning("[docs/delete] 删除失败 %s: %s", path, e)
            return jsonify({"ok": False, "error": "删除失败: " + str(e)[:120]}), 500
        return jsonify({"ok": True})

    @bp.route("/api/docs/batch-delete", methods=["POST"])
    def api_docs_batch_delete():
        """★ 新增1 (2026-07-10): 批量删除 MySQL 虚拟文件（截图/日志等）。

        请求体: {"paths": ["mysql://screenshots/{case_id1}", "mysql://screenshots/{case_id2}", ...]}
        返回: {"ok": True, "deleted": N, "failed": N, "details": [...]}
        """
        ok, err = check_admin_or_permission("admin", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not _current_project():
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        proj = _current_project()
        paths = request.json.get("paths", []) if request.json else []
        if not paths or not isinstance(paths, list):
            return jsonify({"ok": False, "error": "缺少 paths 参数"}), 400

        deleted = 0
        failed = 0
        details = []
        try:
            from src.core.project_purge import purge_specs
            from src.core.versioning import current_version_label

            vlabel = current_version_label(proj)
            for path in paths:
                try:
                    res = purge_specs(proj, [path], version_label=vlabel)
                    if res["errors"]:
                        failed += 1
                        details.append(
                            {"path": path, "ok": False, "error": res["errors"][0][:80]}
                        )
                    else:
                        deleted += 1
                        details.append({"path": path, "ok": True})
                except Exception as e:
                    failed += 1
                    details.append(
                        {"path": path, "ok": False, "error": str(e)[:80]}
                    )
        except Exception as e:
            _log.warning("[docs/batch-delete] 批量删除失败: %s", e)
            return jsonify({"ok": False, "error": "批量删除失败: " + str(e)[:120]}), 500
        return jsonify(
            {"ok": True, "deleted": deleted, "failed": failed, "details": details}
        )
