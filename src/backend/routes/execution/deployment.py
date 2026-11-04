# -*- coding: utf-8 -*-
"""
Deployment 路由
==============
从 execution.py 拆分，注册到 exec_bp Blueprint。
包含：deployment-docs, deployment-versions, last-run, deployment-preview
"""
import logging
from flask import request, jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import exec_bp, _current_project


@exec_bp.route("/api/deployment/docs", methods=["GET"])
def api_deployment_docs():
    """列出项目已有的转测文档。★ MySQL 化：从 source_documents 表读取。"""
    from src.models.permission import can_execute_in_project

    ok, err = can_execute_in_project(None, _current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.core.config_loader import config as _cfg
    from src.core.db_manager import get_connection, release_connection

    docs = []
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT doc_type, char_count, updated_at FROM source_documents "
            "WHERE project=%s AND doc_type=%s",
            (_cfg.project_name, "deployment"),
        ).fetchall()
        for r in rows:
            docs.append(
                {
                    "name": f"转测文档_{r['updated_at'].strftime('%Y%m%d_%H%M%S') if r.get('updated_at') else 'unknown'}.md",
                    "path": f"mysql://source_documents/deployment",
                    "size": int(r.get("char_count", 0) or 0),
                    "mtime": r["updated_at"].timestamp() if r.get("updated_at") else 0,
                }
            )
    except Exception as e:
        _log.warning("[P0-02] deployment docs 读取失败: %s", str(e)[:120])
    finally:
        release_connection(conn)
    return jsonify({"docs": docs})


@exec_bp.route("/api/deployment/versions", methods=["POST"])
def api_deployment_versions():
    """上传转测文档 → 解析版本列表，供前端下拉选择。"""
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return (
            jsonify(
                {"ok": False, "versions": [], "features": [], "error": "文档内容为空"}
            ),
            400,
        )
    try:
        from src.extensions.deployment_matcher import preview_versions

        result = preview_versions(content)
        return jsonify({"ok": True, **result})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "versions": [], "features": [], "error": str(e)}),
            500,
        )


@exec_bp.route("/api/last-run")
def api_last_run():
    """返回上次运行的结果摘要和日志预览。从 MySQL run_logs / action_logs 读取，不再依赖磁盘文件。"""
    try:
        from src.core.config_loader import config
        from src.core.db_manager import get_connection, release_connection

        project_name = getattr(config, "project_name", "")
        conn = None
        _c = None
        try:
            conn = get_connection()
            # 1) 取最近一次 run_id（run_logs 按时间倒序）
            recent = conn.execute(
                "SELECT DISTINCT run_id FROM run_logs WHERE project = %s "
                "ORDER BY id DESC LIMIT 1",
                (project_name,),
            ).fetchone()
            run_id = recent["run_id"] if recent else ""
            if not run_id:
                return jsonify({"found": False})
            # 2) 取该 run_id 的所有日志（按 step 聚合）
            rows = conn.execute(
                "SELECT step_num, log_level, message, created_at FROM run_logs "
                "WHERE run_id = %s ORDER BY id ASC",
                (run_id,),
            ).fetchall()
            # 3) 组装预览：最近 30KB 文本
            log_lines = [
                f"[{r['created_at']}] [{r['log_level']}] [step{r['step_num']}] {r['message']}"
                for r in rows
            ]
            full_log = "\n".join(log_lines)
            preview = full_log[-30000:]
            # 4) 简单统计：总步骤、各步骤状态
            step_set = sorted(set(r["step_num"] for r in rows if r["step_num"]))
            error_count = sum(1 for r in rows if r["log_level"] == "ERROR")
            # 5) 尝试从 action_logs 取 summary
            try:
                _c = get_connection()
                _summary_row = _c.execute(
                    "SELECT detail FROM action_logs WHERE scope_type = 'project' AND scope_id = %s "
                    "ORDER BY id DESC LIMIT 1",
                    (project_name,),
                ).fetchone()
                summary = str(_summary_row["detail"] or "") if _summary_row else ""
            except Exception:
                summary = ""
            finally:
                release_connection(_c)
            return jsonify(
                {
                    "found": True,
                    "run_id": run_id,
                    "steps": step_set,
                    "total_steps": max(step_set) if step_set else 0,
                    "error_count": error_count,
                    "log_preview": preview,
                    "log_size": len(full_log),
                    "summary": summary,
                }
            )
        finally:
            release_connection(conn)
    except Exception as e:
        _log.warning("[last-run] 读取运行结果失败: %s", e)
        return jsonify({"found": False})


@exec_bp.route("/api/deployment/preview", methods=["POST"])
def api_deployment_preview():
    """预览转测文档匹配：传文档路径或内容+版本号 → 返回匹配的测试用例数和 case_ids（不执行）。"""
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    content = data.get("content", "")
    version = data.get("version", "")
    if not path and not content.strip():
        return jsonify({"ok": False, "error": "请提供 path 或 content"}), 400
    try:
        from src.extensions.deployment_matcher import DeploymentMatcher, format_report
        from src.core.excel_handler import excel_handler

        all_intents = excel_handler.read_test_cases()
        matcher = DeploymentMatcher()
        # ★ 全 MySQL：有内容直接用；有 path（mysql:// URI 或虚拟路径）则 resolve_doc 取内容
        if content.strip():
            match_result = matcher.filter_intents_by_deployment(
                doc_content=content,
                intents=all_intents,
                version=version,
            )
        elif path:
            from src.backend.routes.docs_analytics.fetchers.mysql_doc import resolve_doc

            doc = resolve_doc(path, _current_project())
            if not doc or doc.get("kind") not in ("text", "log"):
                return jsonify({"ok": False, "error": "文件不存在: %s" % path}), 404
            match_result = matcher.filter_intents_by_deployment(
                doc_content=doc["content_text"] or "",
                intents=all_intents,
                version=version,
            )
        else:
            return jsonify({"ok": False, "error": "请提供 path 或 content"}), 400
        error = match_result.get("error")
        report = match_result.get("report")
        matched_intents = match_result.get("case_ids", [])
        return jsonify(
            {
                "ok": True,
                "matched_count": match_result.get("matched_count", 0),
                "total_features": match_result.get("total_features", 0),
                "case_ids": matched_intents,
                "report_text": format_report(report, version) if report else "",
                "error": error,
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        _log.error(f"[ERROR] /api/export-report: {e}")
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
