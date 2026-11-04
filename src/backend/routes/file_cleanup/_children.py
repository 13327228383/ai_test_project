# -*- coding: utf-8 -*-
"""子列表懒加载路由：/api/clearable-files/children"""

from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)


def _register_children_routes():
    from src.backend.routes.execution import exec_bp

    @exec_bp.route("/api/clearable-files/children", methods=["GET"])
    def api_clearable_files_children():
        """懒加载子列表数据。?table=requirements|run_logs|..."""
        from src.core.config_loader import get_current_user, config as _cfg
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project
        from src.core.db_manager import get_connection

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        ok, err = check_admin_or_permission("file.clear", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), 403

        table = request.args.get("table", "")
        if not table:
            return jsonify({"ok": False, "error": "缺少 table 参数"}), 400

        proj = _cfg.project_root
        project_name = _cfg.project_name or str(proj)

        try:
            conn = get_connection()
            try:
                children = []

                # ── ★ 按表名分派子列表查询 handler ──
                def _children_upload(table):
                    rows = conn.execute(
                        "SELECT doc_type, filename, file_format, content_hash, char_count, created_at "
                        "FROM source_documents WHERE project=%s AND doc_type LIKE 'upload_%%' "
                        "ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for cr in rows:
                        ch = cr.get("content_hash") or ""
                        doc_type = cr.get("doc_type") or ""
                        fmt = cr.get("file_format") or ""
                        if not fmt and doc_type.startswith("upload_"):
                            core = doc_type[len("upload_") :]
                            parts = core.rsplit("_", 1)
                            fmt = parts[0] if len(parts) > 1 else core
                        name = cr.get("filename") or ""
                        if not name:
                            name = "%s 文件 (%s)" % (
                                fmt.upper(),
                                ch[:8] if ch else "无哈希",
                            )
                        children.append(
                            {
                                "path": "mysql://source_documents/%s" % doc_type,
                                "name": name,
                                "size": int(cr.get("char_count") or 0),
                                "mtime": str(cr.get("created_at", ""))[:16],
                                "fmt": fmt.upper(),
                            }
                        )

                def _children_requirements(table):
                    rows = conn.execute(
                        "SELECT req_id, module, description, updated_at FROM requirements "
                        "WHERE project=%s ORDER BY module, req_id",
                        (project_name,),
                    ).fetchall()
                    for rc in rows:
                        req_id = rc.get("req_id") or ""
                        children.append(
                            {
                                "path": "mysql://requirements/%s" % req_id,
                                "name": "%s — %s"
                                % (req_id, (rc.get("description") or "")[:40]),
                                "size": len(rc.get("description") or "") * 2,
                                "mtime": str(rc.get("updated_at", ""))[:16],
                                "fmt": rc.get("module") or "",
                            }
                        )

                def _children_test_cases(table):
                    rows = conn.execute(
                        "SELECT case_id, title, case_type, risk_level, created_at FROM test_cases "
                        "WHERE project=%s ORDER BY case_type, case_id",
                        (project_name,),
                    ).fetchall()
                    for ic in rows:
                        iid = ic.get("case_id") or ""
                        risk_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                            ic.get("risk_level", ""), ""
                        )
                        children.append(
                            {
                                "path": "mysql://test_cases/%s" % iid,
                                "name": "%s %s" % (iid, (ic.get("title") or "")[:40]),
                                "size": len(ic.get("title") or "") * 4,
                                "mtime": str(ic.get("created_at", ""))[:16],
                                "fmt": "%s %s" % (risk_icon, ic.get("case_type") or ""),
                            }
                        )

                def _children_run_logs(table):
                    rows = conn.execute(
                        "SELECT id, run_id, step_num, log_level, message, created_at FROM run_logs "
                        "WHERE project=%s ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for rl in rows:
                        msg = (rl.get("message") or "")[:40]
                        children.append(
                            {
                                "path": "mysql://run_logs/%s" % str(rl.get("id")),
                                "name": "[%s] %s step=%s %s"
                                % (
                                    rl.get("log_level") or "",
                                    (rl.get("run_id") or "")[:16],
                                    rl.get("step_num") or "",
                                    msg,
                                ),
                                "size": len(rl.get("message") or ""),
                                "mtime": str(rl.get("created_at", ""))[:16],
                                "fmt": rl.get("log_level") or "",
                            }
                        )

                def _children_execution_screenshots(table):
                    rows = conn.execute(
                        "SELECT id, case_id, run_id, step_num, file_size, created_at FROM execution_screenshots "
                        "WHERE project=%s ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for es in rows:
                        children.append(
                            {
                                "path": "mysql://execution_screenshots/%s"
                                % str(es.get("id")),
                                "name": "%s step=%s"
                                % (
                                    (es.get("case_id") or "")[:32],
                                    es.get("step_num") or "",
                                ),
                                "size": int(es.get("file_size") or 0),
                                "mtime": str(es.get("created_at", ""))[:16],
                                "fmt": "PNG",
                            }
                        )

                def _children_rag_chunks(table):
                    rows = conn.execute(
                        "SELECT chunk_id, type, text, created_at FROM rag_chunks "
                        "WHERE scope_type='project' AND scope_id=%s ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for rc in rows:
                        cid = (rc.get("chunk_id") or "")[:40]
                        txt = (rc.get("text") or "")[:40]
                        children.append(
                            {
                                "path": "mysql://rag_chunks/%s"
                                % (rc.get("chunk_id") or ""),
                                "name": "%s — %s" % (cid, txt),
                                "size": len(rc.get("text") or ""),
                                "mtime": str(rc.get("created_at", ""))[:16],
                                "fmt": rc.get("type") or "",
                            }
                        )

                def _children_diff_snapshots(table):
                    rows = conn.execute(
                        "SELECT project, version_label, snapshot_type, item_count, created_at FROM diff_snapshots "
                        "WHERE project=%s ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for ds in rows:
                        children.append(
                            {
                                "path": "mysql://diff_snapshots/%s/%s/%s"
                                % (
                                    ds.get("project") or "",
                                    ds.get("snapshot_type") or "",
                                    ds.get("version_label") or "",
                                ),
                                "name": "%s — %s"
                                % (
                                    ds.get("snapshot_type") or "",
                                    ds.get("version_label") or "",
                                ),
                                "size": int(ds.get("item_count") or 0) * 100,
                                "mtime": str(ds.get("created_at", ""))[:16],
                                "fmt": ds.get("snapshot_type") or "",
                            }
                        )

                def _children_source_hashes(table):
                    rows = conn.execute(
                        "SELECT doc_type, version_label, content_hash, created_at FROM source_hashes "
                        "WHERE project=%s ORDER BY created_at DESC",
                        (project_name,),
                    ).fetchall()
                    for sh in rows:
                        ch = (sh.get("content_hash") or "")[:16]
                        children.append(
                            {
                                "path": "mysql://source_hashes/%s/%s"
                                % (
                                    sh.get("doc_type") or "",
                                    sh.get("version_label") or "",
                                ),
                                "name": "%s: %s" % (sh.get("doc_type") or "", ch),
                                "size": len(sh.get("content_hash") or ""),
                                "mtime": str(sh.get("created_at", ""))[:16],
                                "fmt": sh.get("doc_type") or "",
                            }
                        )

                def _children_generated_reports(table):
                    report_type = table.split("/", 1)[1] if "/" in table else ""
                    where = "WHERE project=%s"
                    params: list = [project_name]
                    if report_type:
                        where += " AND report_type=%s"
                        params.append(report_type)
                    rows = conn.execute(
                        "SELECT id, report_type, version_label, run_id, content_hash, char_count, created_at "
                        "FROM generated_reports %s ORDER BY created_at DESC" % where,
                        tuple(params),
                    ).fetchall()
                    for gr in rows:
                        rt = gr.get("report_type") or ""
                        children.append(
                            {
                                "path": "mysql://generated_reports/%s/%s"
                                % (
                                    rt,
                                    gr.get("version_label") or str(gr.get("id") or ""),
                                ),
                                "name": "[%s] %s" % (rt, (gr.get("run_id") or "")[:40]),
                                "size": int(gr.get("char_count") or 0),
                                "mtime": str(gr.get("created_at", ""))[:16],
                                "fmt": rt,
                            }
                        )

                _CHILDREN_HANDLERS = {
                    "upload": _children_upload,
                    "requirements": _children_requirements,
                    "test_cases": _children_test_cases,
                    "run_logs": _children_run_logs,
                    "execution_screenshots": _children_execution_screenshots,
                    "rag_chunks": _children_rag_chunks,
                    "diff_snapshots": _children_diff_snapshots,
                    "source_hashes": _children_source_hashes,
                    "generated_reports": _children_generated_reports,
                }

                # 按表名分派
                if table.startswith("generated_reports"):
                    _children_generated_reports(table)
                else:
                    handler = _CHILDREN_HANDLERS.get(table)
                    if handler:
                        handler(table)
                    else:
                        return (
                            jsonify({"ok": False, "error": "未知 table: %s" % table}),
                            400,
                        )

                return jsonify(
                    {
                        "ok": True,
                        "table": table,
                        "children": children,
                        "count": len(children),
                    }
                )
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close + close(): %s", _e, exc_info=True)
        except Exception as e:
            import traceback

            traceback.print_exc()
            return jsonify({"ok": False, "error": "加载子列表失败: %s" % str(e)}), 500
