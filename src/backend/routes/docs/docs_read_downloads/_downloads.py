# -*- coding: utf-8 -*-
"""
文档中心 — 文件下载类路由
========================
需求稿、功能清单、测试用例文件下载、需求拆解表。
"""

import logging
from src.core.guarded import guard
from io import BytesIO
from pathlib import Path

from flask import jsonify, request, send_file

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


# ── 需求稿下载 ──


@docs_bp.route("/api/docs/requirement")
def api_docs_requirement():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    req_project = request.args.get("project", "").strip()
    proj, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    check_only = request.args.get("check", "")
    # ★ 修复（2026-06-23）：优先读 MySQL source_documents（all-mysql-no-file-residue 原则）。
    #   原仅读磁盘 raw_confluence_requirements_md，但 Step1 已改为只写 MySQL 不落盘，
    #   导致文档中心恒显示"需求稿尚未生成"。内存物化 BytesIO 不落盘，磁盘仅 fallback。
    try:
        from src.core.db_manager import get_connection as _gc, release_connection as _rc
        from src.core.versioning import current_version_label

        # ★ 版本隔离：读当前 active version 的需求稿，避免旧版本/legacy 内容串读
        _ver_label = current_version_label(proj) or ""
        _conn = _gc()
        try:
            # ★ 性能修复(07-05): doc_type 从 'requirement' 改为 'confluence'
            #   persist.py 已删除 'requirement' 双写，统一读 'confluence'
            if _ver_label:
                _row = _conn.execute(
                    "SELECT content, char_count FROM source_documents "
                    "WHERE project=%s AND doc_type='confluence' AND version_label=%s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (proj, _ver_label),
                ).fetchone()
            else:
                # 无 active 版本时回退到该项目最新一条，避免 blank 版本 FK 不一致
                _row = _conn.execute(
                    "SELECT content, char_count FROM source_documents "
                    "WHERE project=%s AND doc_type='confluence' "
                    "AND version_label!='' "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (proj,),
                ).fetchone()
        finally:
            _rc(_conn)
        if not _row or not _row["content"]:
            if check_only == "1":
                return jsonify({"exists": False})
            return (
                jsonify({"ok": False, "error": "需求稿尚未生成，请先执行 Step 1"}),
                404,
            )
        _content = _row["content"]
        if isinstance(_content, (bytes, bytearray)):
            _content = _content.decode("utf-8", errors="ignore")
        if check_only == "1":
            return jsonify(
                {"exists": True, "size": _row["char_count"] or len(_content)}
            )
        return send_file(
            BytesIO(_content.encode("utf-8")),
            as_attachment=True,
            download_name="需求稿_Confluence.md",
            mimetype="text/markdown; charset=utf-8",
        )
    except Exception as e:
        _log.warning("[docs/requirement] MySQL 读取失败: %s", e)
        return jsonify({"ok": False, "error": "需求稿读取失败: %s" % str(e)[:120]}), 500


# ── 功能清单下载（★ 2026-06-30: 替代旧 nav_map，数据来源 figma_pages）──


@docs_bp.route("/api/docs/feature-list")
def api_docs_feature_list():
    """导出功能清单 Excel — 数据来源 figma_pages + figma_nodes 表。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project, _temporary_project

    req_project = request.args.get("project", "").strip()
    proj, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status

    # ★ 依赖全局 config.project_version，需临时切换项目上下文
    with _temporary_project(proj):
        try:
            from src.core.config_loader import config

            version_label = str(getattr(config, "project_version", "") or "")
        except Exception:
            version_label = ""

        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                # 1. 加载 figma_pages
                pages = conn.execute(
                    "SELECT page_id, name, canvas, page_type FROM figma_pages "
                    "WHERE project=%s AND version_label=%s ORDER BY canvas, name",
                    (proj, version_label),
                ).fetchall()

                # 2. 加载 figma_nodes（页面内元素）
                nodes = conn.execute(
                    "SELECT n.page_id, n.name, n.node_type, n.text_content, "
                    "p.name as page_name, p.canvas "
                    "FROM figma_nodes n JOIN figma_pages p ON n.page_id = p.page_id "
                    "WHERE n.project=%s AND n.version_label=%s "
                    "AND (text_content IS NOT NULL OR n.name IS NOT NULL) "
                    "ORDER BY p.canvas, p.name, n.name "
                    "LIMIT 2000",
                    (proj, version_label),
                ).fetchall()
            finally:
                with guard("api_docs_feature_list: conn.close()", log=_log):
                    conn.close()
        except Exception as e:
            _log.warning("[feature-list] 数据库查询失败: %s", e)
            return (
                jsonify({"ok": False, "error": "功能清单查询失败: %s" % str(e)[:120]}),
                500,
            )

    # 3. 按层级构建行数据
    rows = []
    if nodes:
        # 有节点数据：系统 → 画布 → 页面 → 元素 → 元素类型
        for n in nodes:
            tn = (n.get("text_content") or "").strip()
            elem_text = n.get("name", "") or ""
            if tn and tn != elem_text:
                elem_text += " " + tn[:50]
            rows.append(
                [
                    proj,
                    n.get("canvas", "") or "",
                    n.get("page_name", "") or "",
                    elem_text[:100],
                    n.get("node_type", "") or "",
                ]
            )
    elif pages:
        # 只有页面数据：系统 → 画布 → 页面 → 页面类型
        for p in pages:
            rows.append(
                [
                    proj,
                    p.get("canvas", "") or "",
                    p.get("name", "") or "",
                    p.get("page_type", "") or "",
                ]
            )
    else:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "暂无 Figma 页面数据，请先执行 Step 1 拉取 Figma 设计稿",
                }
            ),
            404,
        )

    # 4. 自适应表头
    max_depth = max(len(r) for r in rows) if rows else 2
    HEADERS_POOL = [
        "系统",
        "模块/画布",
        "功能/页面",
        "元素/组件",
        "子元素/操作",
        "详情",
    ]
    headers = HEADERS_POOL[:max_depth]

    # 5. 生成 Excel
    try:
        import openpyxl
    except ImportError:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "缺少 openpyxl 依赖，请安装: pip install openpyxl",
                }
            ),
            500,
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "功能清单"

    # 表头样式
    from openpyxl.styles import Font, Alignment, PatternFill

    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(
        start_color="D9E1F2", end_color="D9E1F2", fill_type="solid"
    )

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row_data in enumerate(rows, 2):
        padded = row_data + [""] * (max_depth - len(row_data))
        for col_idx, val in enumerate(padded, 1):
            ws.cell(row=row_idx, column=col_idx, value=val)

    # 自适应列宽
    for col_idx in range(1, max_depth + 1):
        max_width = len(str(headers[col_idx - 1])) * 2
        for row_data in rows:
            cell_val = str(row_data[col_idx - 1]) if col_idx <= len(row_data) else ""
            max_width = max(max_width, min(len(cell_val) * 1.2, 50))
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = (
            max_width
        )

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name=f"{proj}_功能清单.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── 测试用例文件下载（含 XMind） ──


@docs_bp.route("/api/docs/intent-asset-file/<name>")
def api_docs_case_asset_file(name):
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
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
    safe_name = Path(name).name
    is_xlsx = safe_name.startswith("Validation_Intent") and safe_name.endswith(".xlsx")
    is_xmind = safe_name.startswith("Test_Design") and safe_name.endswith(".xmind")
    if not is_xlsx and not is_xmind:
        return jsonify({"ok": False, "error": "无效文件名"}), 400
    if is_xmind:
        # ★ XMind 生成器暂未 MySQL 化，提示用 xlsx 导出
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "XMind 格式暂不支持在线下载，请用测试用例 Excel 导出",
                }
            ),
            404,
        )
    # ★ 全走 MySQL：从 test_cases 表现查生成 xlsx（复用 store.export_to_excel_bytes）
    from src.pipeline.utils.case_store import case_store

    with _temporary_project(project_name):
        intents = case_store.list_intents() or []
        if not intents:
            return (
                jsonify({"ok": False, "error": "测试用例尚未生成，请先执行 Step 3"}),
                404,
            )
        try:
            excel_bytes = case_store.export_to_excel_bytes()
        except Exception as e:
            return (
                jsonify(
                    {"ok": False, "error": "生成测试用例文件失败: %s" % str(e)[:120]}
                ),
                500,
            )
    return send_file(
        BytesIO(excel_bytes),
        as_attachment=True,
        download_name=safe_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── 需求拆解表 ──


@docs_bp.route("/api/docs/breakdown")
def api_docs_breakdown():
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
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
    # ★ 全走 MySQL：从 requirements 表现查生成 xlsx（复用 store.export_to_excel_bytes）
    from src.pipeline.utils.requirement_store import requirement_store

    check_only = request.args.get("check", "")
    with _temporary_project(project_name):
        reqs = requirement_store.list_requirements() or []
        if check_only == "1":
            return jsonify({"exists": bool(reqs), "size": len(reqs)})
        if not reqs:
            return (
                jsonify({"ok": False, "error": "需求拆解表尚未生成，请先执行 Step 2"}),
                404,
            )
        try:
            excel_bytes = requirement_store.export_to_excel_bytes()
        except Exception as e:
            return (
                jsonify(
                    {"ok": False, "error": "生成需求拆解表失败: %s" % str(e)[:120]}
                ),
                500,
            )
    return send_file(
        BytesIO(excel_bytes),
        as_attachment=True,
        download_name="requirements_breakdown.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
