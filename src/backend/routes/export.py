# -*- coding: utf-8 -*-
"""
导出路由 Blueprint
==================
从 MySQL 读取数据，按需生成 Excel / Markdown 文件供下载。
运行时只写 MySQL，导出时从库生成格式文件。

路由：
    GET /api/export/requirements  →  requirements.xlsx
    GET /api/export/validation-intents    →  test_cases.xlsx
    GET /api/export/report        →  Test_Report.md
"""

import logging
from datetime import datetime
from flask import Blueprint, jsonify, send_file
from src.models.permission import check_admin_or_permission

_log = logging.getLogger(__name__)
export_bp = Blueprint("export", __name__)


def _get_project() -> str:
    from src.backend.state import _current_project as _cp

    return _cp() or ""


def _check_access():
    """检查用户是否有 docs.export 权限。"""
    ok, err = check_admin_or_permission("docs.export", project=_get_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    return None


def _flatten_cases(intents):
    """将 payload 中的用例字段展平到顶层（导出 xlsx / JSON 预览共用）。"""
    flat_rows = []
    for c in intents:
        row = dict(c)
        payload = row.pop("payload", {}) or {}
        if isinstance(payload, dict):
            for k in (
                "title",
                "risk_level",
                "case_type",
                "precondition",
                "case_actions_json",
                "expected_outcome",
                "catalog_name",
            ):
                if k not in row or not row.get(k):
                    row[k] = payload.get(k, "")
        flat_rows.append(row)
    return flat_rows


CASE_EXPORT_HEADERS = [
    "case_id",
    "title",
    "risk_level",
    "case_type",
    "catalog_name",
    "precondition",
    "case_actions_json",
    "expected_outcome",
    "verification_status",
]

REQ_EXPORT_HEADERS = [
    "req_id",
    "module",
    "description",
    "dependency_req_id",
    "review_status",
    "is_generated",
]


# ── 1. 导出需求 Excel ──


@export_bp.route("/api/export/requirements", methods=["GET"])
def export_requirements():
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.pipeline.utils.requirement_store import requirement_store

        reqs = requirement_store.list_requirements()
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取需求数据失败: {e}"}), 500

    if not reqs:
        return jsonify({"ok": False, "error": "当前项目无需求数据可导出"}), 404

    return _build_xlsx_response(
        rows=reqs,
        headers=REQ_EXPORT_HEADERS,
        filename=f"{project}_requirements_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
        sheet_name="需求列表",
    )


# ── 2. 导出国例 Excel ──


@export_bp.route("/api/export/validation-intents", methods=["GET"])
def export_test_cases():
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.pipeline.utils.case_store import case_store

        intents = case_store.list_intents()
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取测试用例数据失败: {e}"}), 500

    if not intents:
        return jsonify({"ok": False, "error": "当前项目无测试用例数据可导出"}), 404

    # 展平 payload 中的字段到顶层（与 JSON 预览共用同一逻辑）
    flat_rows = _flatten_cases(intents)

    return _build_xlsx_response(
        rows=flat_rows,
        headers=CASE_EXPORT_HEADERS,
        filename=f"{project}_test_cases_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
        sheet_name="测试用例",
    )


# ── 3. 导出最新报告 Markdown ──


@export_bp.route("/api/export/report", methods=["GET"])
def export_report():
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.users.reports import get_generated_report

        content = get_generated_report(project, "report")
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取报告数据失败: {e}"}), 500

    if not content:
        return jsonify({"ok": False, "error": "当前项目无报告数据可导出"}), 404

    import io

    safe_name = project.replace("/", "-").replace("\\", "-")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bio = io.BytesIO(content.encode("utf-8"))
    return send_file(
        bio,
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=f"Test_Report_{safe_name}_{ts}.md",
    )


# ── 4. 导出覆盖矩阵 Excel ──


@export_bp.route("/api/export/coverage", methods=["GET"])
def export_coverage():
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.pipeline.utils.coverage_store import coverage_store

        # 先检查是否有覆盖数据
        if not coverage_store.has_coverage():
            return jsonify({"ok": False, "error": "当前项目无覆盖数据可导出"}), 404

        import io

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # ★ 纯内存 BytesIO，服务器不落盘（all-mysql-no-file-residue）
        try:
            excel_bytes = coverage_store.export_to_excel_bytes()
        except Exception as e:
            return jsonify({"ok": False, "error": f"导出覆盖矩阵失败: {e}"}), 500
        return send_file(
            io.BytesIO(excel_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{project}_coverage_{ts}.xlsx",
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"导出覆盖矩阵失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════════
# 5. 导出导航树功能清单 Excel
# ═══════════════════════════════════════════════════════════════════


@export_bp.route("/api/export/navigation-tree", methods=["GET"])
def export_navigation_tree():
    """导出导航树功能清单为 Excel（带模块颜色和依赖关系）。"""
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.users.manager import get_active_version_label

        version_label = get_active_version_label(project) or ""
        if not version_label:
            return jsonify({"ok": False, "error": "请先选择版本"}), 400

        from src.pipeline.utils.navigation_tree_builder import (
            build_navigation_tree,
            export_tree_to_excel,
        )

        # 构建导航树
        tree_result = build_navigation_tree(project, version_label)
        if not tree_result.get("success"):
            return (
                jsonify(
                    {"ok": False, "error": tree_result.get("error", "导航树构建失败")}
                ),
                500,
            )

        # 导出 Excel
        excel_bytes = export_tree_to_excel(
            tree_result["tree"],
            tree_result.get("dependencies", []),
            project,
        )

        if not excel_bytes:
            return jsonify({"ok": False, "error": "Excel 生成失败"}), 500

        import io
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            io.BytesIO(excel_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{project}_导航功能清单_{ts}.xlsx",
        )

    except Exception as e:
        return jsonify({"ok": False, "error": f"导出导航树失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════════
# 6. 获取导航树 JSON（预览）
# ═══════════════════════════════════════════════════════════════════


@export_bp.route("/api/navigation-tree/preview", methods=["GET"])
def preview_navigation_tree():
    """获取导航树 JSON（供前端预览）。"""
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        from src.users.manager import get_active_version_label

        version_label = get_active_version_label(project) or ""
        if not version_label:
            return jsonify({"ok": False, "error": "请先选择版本"}), 400

        from src.pipeline.utils.navigation_tree_builder import get_navigation_tree_json

        result = get_navigation_tree_json(project, version_label)
        if not result.get("success"):
            return (
                jsonify({"ok": False, "error": result.get("error", "导航树获取失败")}),
                500,
            )

        return jsonify(
            {
                "ok": True,
                "tree_json": result["tree_json"],
                "statistics": result["statistics"],
                "dependencies": result["dependencies"],
                "modules": result["modules"],
            }
        )

    except Exception as e:
        return jsonify({"ok": False, "error": f"获取导航树失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════════
# 7. 导出数据 JSON 预览（统一处理需求/用例/报告/覆盖率）
# ═══════════════════════════════════════════════════════════════════


@export_bp.route("/api/export/<path:type>/preview", methods=["GET"])
def preview_export(type):
    """按类型返回可读的 JSON 预览，供前端「预览」按钮使用。

    与对应 export_* 路由使用同源数据（MySQL 唯一事实源），确保预览内容
    与导出一致；不落入二进制。导航树走 /api/navigation-tree/preview。
    """
    _err = _check_access()
    if _err:
        return _err

    project = _get_project()
    if not project:
        return jsonify({"ok": False, "error": "请先选择项目"}), 400

    try:
        if type == "requirements":
            from src.pipeline.utils.requirement_store import requirement_store

            reqs = requirement_store.list_requirements()
            if not reqs:
                return jsonify({"ok": False, "error": "当前项目无需求数据可导出"}), 404
            return jsonify(
                {"ok": True, "type": type, "columns": REQ_EXPORT_HEADERS, "rows": reqs}
            )

        if type == "validation-intents":
            from src.pipeline.utils.case_store import case_store

            intents = case_store.list_intents()
            if not intents:
                return jsonify({"ok": False, "error": "当前项目无测试用例数据可导出"}), 404
            flat_rows = _flatten_cases(intents)
            return jsonify(
                {
                    "ok": True,
                    "type": type,
                    "columns": CASE_EXPORT_HEADERS,
                    "rows": flat_rows,
                }
            )

        if type == "report":
            from src.users.reports import get_generated_report

            content = get_generated_report(project, "report") or ""
            if not content:
                return jsonify({"ok": False, "error": "当前项目无报告数据可导出"}), 404
            return jsonify({"ok": True, "type": type, "content": content})

        if type == "coverage":
            from src.pipeline.utils.coverage_store import coverage_store

            cov = coverage_store.load_coverage()
            if not cov:
                return jsonify({"ok": False, "error": "当前项目无覆盖数据可导出"}), 404
            return jsonify({"ok": True, "type": type, "content": cov})

        return jsonify({"ok": False, "error": f"不支持的预览类型: {type}"}), 400
    except Exception as e:
        _log.exception("[export] %s 预览失败", type)
        return jsonify({"ok": False, "error": f"预览失败: {e}"}), 500


# ═══════════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════════


def _build_xlsx_response(rows, headers, filename, sheet_name):
    """从行数据构建 xlsx 并返回 Flask 响应。"""
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]  # Excel sheet name max 31 chars

    # 表头
    for ci, h in enumerate(headers, 1):
        ws.cell(row=1, column=ci, value=h)

    # 数据行
    for ri, row in enumerate(rows, 2):
        for ci, h in enumerate(headers, 1):
            val = row.get(h, "")
            if val is None:
                val = ""
            elif not isinstance(val, (str, int, float, bool)):
                val = str(val)
            ws.cell(row=ri, column=ci, value=val)

    ws.auto_filter.ref = ws.dimensions

    bio = io.BytesIO()
    wb.save(bio)
    wb.close()
    bio.seek(0)

    return send_file(
        bio,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
