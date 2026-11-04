# -*- coding: utf-8 -*-
"""可清除文件列表路由：/api/clearable-files"""

from flask import jsonify
import logging
from collections import Counter

_log = logging.getLogger(__name__)


def _register_list_routes():
    from src.backend.routes.execution import exec_bp

    @exec_bp.route("/api/clearable-files", methods=["GET"])
    def api_clearable_files():
        """返回可清除的数据列表（全部来自 MySQL，不再扫描磁盘文件）。"""
        from src.core.config_loader import get_current_user
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        ok, err = check_admin_or_permission("file.clear", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), 403

        try:
            from src.core.config_loader import config as _cfg
            from src.core.db_manager import get_connection

            proj = _cfg.project_root
            project_name = _cfg.project_name or str(proj)

            items = []
            conn = get_connection()
            try:
                # ── ▸ 数据拉取阶段：Confluence / YApi / Figma / 用户上传 ──
                # source_documents 各类文档 → 按来源分到不同阶段
                _sd_group_source = {
                    "confluence": ("数据拉取", "Confluence拉取", "Confluence 需求稿"),
                    "yapi": ("数据拉取", "YApi拉取", "YApi 接口文档"),
                    "figma": ("数据拉取", "Figma拉取", "Figma 设计稿"),
                    "xmind": ("系统缓存", "导出", "测试设计导出 (XMind)"),
                    "smoke_excel": ("系统缓存", "导出", "冒烟用例 Excel"),
                }
                for doc_type, (grp, src, label) in _sd_group_source.items():
                    r = conn.execute(
                        "SELECT COUNT(*) as cnt, SUM(char_count) as total_chars, "
                        "MAX(updated_at) as up FROM source_documents "
                        "WHERE project=%s AND doc_type=%s",
                        (project_name, doc_type),
                    ).fetchone()
                    cnt = r["cnt"] if r else 0
                    if cnt > 0:
                        items.append(
                            {
                                "path": "mysql://source_documents/%s" % doc_type,
                                "name": "%s (%d 份)" % (label, cnt),
                                "size": int(r.get("total_chars") or 0),
                                "mtime": str(r.get("up", ""))[:16],
                                "group": grp,
                                "source": src,
                                "desc": label,
                                "mysql_table": "source_documents",
                                "doc_type": doc_type,
                            }
                        )

                # ── ▸ 系统缓存阶段：流水线生成报告（report / html_report / coverage_report）──
                _gen_report_labels = {
                    "report": "Markdown 测试报告",
                    "html_report": "HTML 测试报告",
                    "coverage_report": "覆盖率报告",
                }
                for report_type, label in _gen_report_labels.items():
                    r = conn.execute(
                        "SELECT COUNT(*) as cnt, SUM(char_count) as total_chars, "
                        "MAX(updated_at) as up FROM generated_reports "
                        "WHERE project=%s AND report_type=%s",
                        (project_name, report_type),
                    ).fetchone()
                    cnt = r["cnt"] if r else 0
                    if cnt > 0:
                        items.append(
                            {
                                "path": "mysql://generated_reports/%s" % report_type,
                                "name": "%s (%d 份)" % (label, cnt),
                                "size": int(r.get("total_chars") or 0),
                                "mtime": str(r.get("up", ""))[:16],
                                "group": "系统缓存",
                                "source": "导出",
                                "desc": label,
                                "mysql_table": "generated_reports",
                                "doc_type": report_type,
                                "has_children": True,
                                "children_key": "generated_reports/%s" % report_type,
                                "children_count": cnt,
                            }
                        )

                # ── ▸ 数据拉取阶段：Figma MCP 缓存 ──
                r = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(updated_at) as up FROM figma_mcp_cache WHERE project=%s",
                    (project_name,),
                ).fetchone()
                figma_cnt = r["cnt"] if r else 0
                if figma_cnt > 0:
                    items.append(
                        {
                            "path": "mysql://figma_mcp_cache",
                            "name": "Figma MCP 缓存 (%d 条)" % figma_cnt,
                            "size": figma_cnt * 1024,
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "数据拉取",
                            "source": "Figma拉取",
                            "desc": "Figma 设计稿 MCP 缓存",
                            "mysql_table": "figma_mcp_cache",
                        }
                    )

                # ── ▸ 数据拉取阶段：用户上传文档 ──
                r = conn.execute(
                    "SELECT COUNT(*) as cnt, SUM(char_count) as total_chars, "
                    "MAX(updated_at) as up FROM source_documents "
                    "WHERE project=%s AND doc_type LIKE 'upload_%%'",
                    (project_name,),
                ).fetchone()
                upload_cnt = r["cnt"] if r else 0
                if upload_cnt > 0:
                    # 纯格式名
                    fmt_rows = conn.execute(
                        "SELECT DISTINCT doc_type FROM source_documents "
                        "WHERE project=%s AND doc_type LIKE 'upload_%%'",
                        (project_name,),
                    ).fetchall()
                    # 格式统计（按数量降序，显示 EXCEL:2 | TEXT:1）
                    fmt_counts = Counter()
                    for row in fmt_rows:
                        dt = row["doc_type"]
                        core = dt[len("upload_") :]
                        parts = core.rsplit("_", 1)
                        f = parts[0].upper() if len(parts) > 1 else core.upper()
                        fmt_counts[f] += 1
                    fmt_sorted = sorted(fmt_counts.items(), key=lambda x: -x[1])
                    fmt_label = " | ".join("%s:%d" % (k, v) for k, v in fmt_sorted[:5])
                    if len(fmt_sorted) > 5:
                        fmt_label += " ..."
                    if not fmt_label:
                        fmt_label = "文件"
                    fmt_desc_label = ", ".join(k for k, v in fmt_sorted[:4])

                    items.append(
                        {
                            # ★ P2: MySQL doc URI 走 constants
                            "path": "mysql://source_documents/upload",
                            "name": "上传文档 (%d 份, %s)" % (upload_cnt, fmt_label),
                            "size": int(r.get("total_chars") or 0),
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "数据拉取",
                            "source": "用户上传",
                            "desc": "补充文档（%s）" % fmt_desc_label,
                            "mysql_table": "source_documents",
                            "doc_type": "upload",
                            "has_children": True,
                            "children_key": "upload",
                            "children_count": upload_cnt,
                        }
                    )

                # ── ▸ AI 生成阶段：需求拆解 + 测试用例 ──
                r = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(updated_at) as up FROM requirements WHERE project=%s",
                    (project_name,),
                ).fetchone()
                if r and r["cnt"] > 0:
                    items.append(
                        {
                            "path": "mysql://requirements",
                            "name": "需求拆解 (%d 条)" % r["cnt"],
                            "size": r["cnt"] * 2048,
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "AI 生成",
                            "source": "AI 生成",
                            "desc": "需求拆解表 (Step 2)",
                            "mysql_table": "requirements",
                            "has_children": True,
                            "children_key": "requirements",
                            "children_count": r["cnt"],
                        }
                    )

                r = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(created_at) as up FROM test_cases WHERE project=%s",
                    (project_name,),
                ).fetchone()
                if r and r["cnt"] > 0:
                    items.append(
                        {
                            "path": "mysql://test_cases",
                            "name": "测试用例 (%d 条)" % r["cnt"],
                            "size": r["cnt"] * 4096,
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "AI 生成",
                            "source": "AI 生成",
                            "desc": "测试用例表 (Step 3)",
                            "mysql_table": "test_cases",
                            "has_children": True,
                            "children_key": "test_cases",
                            "children_count": r["cnt"],
                        }
                    )

                # ── ▸ 执行产物阶段：截图 + 日志 ──
                r = conn.execute(
                    "SELECT COUNT(*) as cnt, SUM(LENGTH(image_data)) as total_bytes, "
                    "MAX(created_at) as up FROM execution_screenshots WHERE project=%s",
                    (project_name,),
                ).fetchone()
                ss_cnt = r["cnt"] if r else 0
                if ss_cnt > 0:
                    items.append(
                        {
                            "path": "mysql://execution_screenshots",
                            "name": "失败截图 (%d 张)" % ss_cnt,
                            "size": int(r.get("total_bytes") or 0),
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "执行产物",
                            "source": "执行记录",
                            "desc": "Step4 失败截图",
                            "mysql_table": "execution_screenshots",
                            "has_children": True,
                            "children_key": "execution_screenshots",
                            "children_count": ss_cnt,
                        }
                    )

                r = conn.execute(
                    "SELECT COUNT(*) as cnt, SUM(LENGTH(message)) as total_bytes, "
                    "MAX(created_at) as up FROM run_logs WHERE project=%s",
                    (project_name,),
                ).fetchone()
                log_cnt = r["cnt"] if r else 0
                if log_cnt > 0:
                    items.append(
                        {
                            "path": "mysql://run_logs",
                            "name": "执行日志 (%d 条)" % log_cnt,
                            "size": int(r.get("total_bytes") or 0),
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "执行产物",
                            "source": "执行记录",
                            "desc": "流水线执行日志",
                            "mysql_table": "run_logs",
                            "has_children": True,
                            "children_key": "run_logs",
                            "children_count": log_cnt,
                        }
                    )

                # ── ▸ 系统缓存：RAG 分块 / 哈希 / 快照 ──
                r = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(updated_at) as up FROM rag_chunks WHERE scope_type='project' AND scope_id=%s",
                    (project_name,),
                ).fetchone()
                rag_cnt = r["cnt"] if r else 0
                if rag_cnt > 0:
                    items.append(
                        {
                            "path": "mysql://rag_chunks",
                            "name": "RAG 索引分块 (%d 个)" % rag_cnt,
                            "size": rag_cnt * 512,
                            "mtime": str(r.get("up", ""))[:16],
                            "group": "系统缓存",
                            "source": "系统缓存",
                            "desc": "RAG 语义检索索引",
                            "mysql_table": "rag_chunks",
                            "has_children": True,
                            "children_key": "rag_chunks",
                            "children_count": rag_cnt,
                        }
                    )

                for tbl, desc, ch_key in [
                    ("diff_snapshots", "变更差异快照", "diff_snapshots"),
                    ("source_hashes", "数据源哈希", "source_hashes"),
                ]:
                    try:
                        r = conn.execute(
                            "SELECT COUNT(*) as cnt, MAX(updated_at) as up FROM %s WHERE project=%%s"
                            % tbl,
                            (project_name,),
                        ).fetchone()
                        c = r["cnt"] if r else 0
                        if c > 0:
                            items.append(
                                {
                                    "path": "mysql://%s" % tbl,
                                    "name": "%s (%d 条)" % (desc, c),
                                    "size": c * 512,
                                    "mtime": str(r.get("up", ""))[:16],
                                    "group": "系统缓存",
                                    "source": "系统缓存",
                                    "desc": desc,
                                    "mysql_table": tbl,
                                    "has_children": True,
                                    "children_key": ch_key,
                                    "children_count": c,
                                }
                            )
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            # ── 依赖关系：AI 生成 → 数据拉取，执行产物 → AI 生成 ──
            _dep_map = {"AI 生成": "数据拉取", "执行产物": "AI 生成"}
            for item in items:
                g = item.get("group", "")
                if g in _dep_map:
                    item["depends_on_group"] = _dep_map[g]

            # ── 阶段分组（先分阶段，来源在卡片标签显示）──
            _group_key_map = {
                "数据拉取": "stage_pull",
                "AI 生成": "stage_generate",
                "执行产物": "stage_execute",
                "系统缓存": "stage_cache",
            }
            group_order = ["数据拉取", "AI 生成", "执行产物", "系统缓存"]
            grouped = {}
            for item in items:
                g = item.get("group", "其他")
                if g not in grouped:
                    grouped[g] = {
                        "name": g,
                        "group_key": _group_key_map.get(g, g),
                        "items": [],
                    }
                grouped[g]["items"].append(item)

            return jsonify(
                {
                    "ok": True,
                    "groups": [grouped[g] for g in group_order if g in grouped],
                    "total_count": len(items),
                    "total_size": sum(it.get("size", 0) for it in items),
                    "storage_engine": "mysql",
                }
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            return jsonify({"ok": False, "error": "扫描失败: %s" % str(e)}), 500
