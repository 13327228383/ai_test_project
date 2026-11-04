# -*- coding: utf-8 -*-
"""
统计路由 — api_docs_execution_stats
"""

from flask import jsonify, request
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission

from src.backend.routes.docs_analytics import (
    _build_module_stats,
    _build_executor_stats,
    _build_type_stats,
    _build_risk_level_stats,
    _build_version_stats,
)

from ._export import _export_stats_excel, _export_stats_docx, _export_stats_pdf


@docs_bp.route("/api/docs/execution-stats")
def api_docs_execution_stats():
    ok, err = check_admin_or_permission("docs.export", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project, _temporary_project

    req_project = request.args.get("project", "").strip()
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    dims_raw = request.args.get("dims", "module,executor,case_type,risk_level,version")
    dims = [x for d in dims_raw.split(",") if (x := d.strip())]
    fmt = request.args.get("format", "md").lower().strip()  # ★ 新增格式参数
    try:
        from src.core.excel_handler import excel_handler
        from src.users.manager import get_execution_status_map

        # ★ excel_handler.read_test_cases() 依赖全局项目，需临时切换
        with _temporary_project(project_name):
            intents = excel_handler.read_test_cases()
            if not intents:
                return jsonify({"ok": False, "error": "无测试用例数据"}), 404

            cids = [c.get("case_id", "") for c in intents if c.get("case_id")]
            status_map = get_execution_status_map(cids, project_name)
        for c in intents:
            c["verification_status"] = status_map.get(c.get("case_id", ""), "")

        now = __import__("datetime").datetime.now()
        total = len(intents)
        passed = 0
        failed = 0
        pending = 0
        skipped = 0
        for s in status_map.values():
            if s == "PASS":
                passed += 1
            elif s == "FAIL":
                failed += 1
            elif s == "SKIP":
                skipped += 1
        pending = total - passed - failed - skipped
        executed = total - pending
        pass_rate = "%.1f%%" % (passed / executed * 100) if executed > 0 else "未执行"

        # ★ 收集所有统计段落
        sections = []
        overview = {
            "total": total,
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
            "pass_rate": pass_rate,
        }

        if "module" in dims:
            sections.append(("module", _build_module_stats(intents)))
        if "executor" in dims:
            sections.append(("executor", _build_executor_stats(intents)))
        if "case_type" in dims:
            sections.append(("case_type", _build_type_stats(intents)))
        if "risk_level" in dims:
            sections.append(("risk_level", _build_risk_level_stats(intents)))
        if "version" in dims:
            sections.append(("version", _build_version_stats()))

        # ── 按格式输出 ──
        if fmt == "xlsx":
            return _export_stats_excel(overview, sections, now)
        elif fmt == "docx":
            return _export_stats_docx(overview, sections, now)
        elif fmt == "pdf":
            return _export_stats_pdf(overview, sections, now)

        # ── 默认 Markdown ──
        from io import BytesIO
        from flask import send_file

        lines = [
            "# 测试执行统计报告",
            "",
            "**项目**: %s" % project_name,
            "**生成时间**: %s" % now.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "---",
            "",
            "## 📊 执行概览",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            "| 测试用例总数 | %d |" % total,
            "| 已验证 | %d |" % executed,
            "| 通过 | %d |" % passed,
            "| 失败 | %d |" % failed,
            "| 跳过 | %d |" % skipped,
            "| 待验证 | %d |" % pending,
            "| 达成率 | %s |" % pass_rate,
            "",
            "---",
            "",
        ]
        for _, sec_lines in sections:
            lines += sec_lines
        lines += ["---", "", "*报告由 E2E 智能测试引擎自动生成*"]
        # ★ 纯内存 BytesIO，不落盘（all-mysql-no-file-residue）
        md_bytes = BytesIO("\n".join(lines).encode("utf-8"))
        dl_name = "Test_Stats_%s_%s.md" % (project_name, now.strftime("%Y%m%d_%H%M%S"))
        md_bytes.seek(0)
        return send_file(
            md_bytes,
            as_attachment=True,
            download_name=dl_name,
            mimetype="text/markdown; charset=utf-8",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
