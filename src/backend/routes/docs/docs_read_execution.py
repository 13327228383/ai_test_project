# -*- coding: utf-8 -*-
"""
文档中心 — 执行结果导出路由
==========================
执行结果 ZIP 下载。
"""

import zipfile
from io import BytesIO
from flask import jsonify, request, send_file
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
_SEND_FILE_MEMORY_LIMIT = 50 * 1024 * 1024


@docs_bp.route("/api/docs/execution-result")
def api_docs_execution_result():
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
    executor = request.args.get("executor", "").strip()
    case_asset_file = request.args.get("case_asset_file", "").strip()
    try:
        from src.core.excel_handler import excel_handler, ExcelHandler
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        # ★ excel_handler.read_test_cases() 依赖全局项目，需临时切换
        with _temporary_project(project_name):
            intents = excel_handler.read_test_cases()
            if not intents:
                return jsonify({"ok": False, "error": "无测试用例数据"}), 404

            # ── 从 MySQL 获取执行记录（截图路径 + 失败原因） ──
            exec_records = {}  # case_id → record
            c = None
            try:
                from src.users.manager import _conn

                c = _conn()
                try:
                    from src.core.versioning import current_version_id

                    active_version_id = current_version_id(project_name)
                except Exception:
                    active_version_id = 0
                if executor:
                    rows = c.execute(
                        "SELECT * FROM executions WHERE project=%s AND version_id=%s AND user_id=%s ORDER BY executed_at DESC",
                        (project_name, active_version_id, executor),
                    ).fetchall()
                else:
                    # ★ 方案B 修复数据泄漏：无 executor 参数时默认使用当前登录用户
                    #   原实现：返回所有用户的执行记录（数据泄漏）
                    #   新实现：默认按当前登录用户过滤
                    try:
                        from src.core.config_loader import get_current_user

                        _current_uid = get_current_user() or ""
                    except Exception:
                        _current_uid = ""
                    if _current_uid:
                        rows = c.execute(
                            "SELECT * FROM executions WHERE project=%s AND version_id=%s AND user_id=%s ORDER BY executed_at DESC",
                            (project_name, active_version_id, _current_uid),
                        ).fetchall()
                    else:
                        rows = c.execute(
                            "SELECT * FROM executions WHERE project=%s AND version_id=%s ORDER BY executed_at DESC",
                            (project_name, active_version_id),
                        ).fetchall()
                for r in rows:
                    rec = dict(r)
                    cid = rec.get("case_id", "")
                    if cid and cid not in exec_records:
                        exec_records[cid] = rec
            except Exception as _e:
                _log.warning("[execution-result] 读取执行记录失败: %s", _e)
            finally:
                if c:
                    try:
                        c.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)
            if executor:
                intents = [c for c in intents if c.get("case_id", "") in exec_records]

            # ── 从 MySQL 加载失败截图 ──
            collected_ss = {}  # case_id → png bytes (from MySQL execution_screenshots)
            ss_links = {}  # case_id → display name in Excel
            _mysql_c = None
            try:
                from src.core.db_manager import get_connection as _gc

                _mysql_c = _gc()
                _fail_case_ids = [
                    cid
                    for cid, rec in exec_records.items()
                    if str(rec.get("status", "")).strip().upper() == "FAIL"
                ]
                if _fail_case_ids:
                    _placeholders = ",".join(["%s"] * len(_fail_case_ids))
                    _ss_rows = _mysql_c.execute(
                        "SELECT case_id, image_data, created_at FROM execution_screenshots "
                        "WHERE project = %s AND case_id IN (%s) "
                        "ORDER BY id DESC" % (_placeholders,),
                        (project_name, *_fail_case_ids),
                    ).fetchall()
                    for _sr in _ss_rows:
                        _cid = _sr["case_id"]
                        if _cid not in collected_ss:
                            collected_ss[_cid] = bytes(_sr["image_data"])
                            ss_links[_cid] = "screenshots/fail_%s.png" % _cid
            except Exception as _ss_e:
                _log.warning("[execution-result] 从 MySQL 加载截图失败: %s", _ss_e)
            finally:
                if _mysql_c:
                    try:
                        _mysql_c.close()
                    except Exception as _pass_e:
                        _log.warning("非关键异常: %s", _pass_e, exc_info=True)

        # ── 构建 Excel（20列）—— 以下纯内存操作，不依赖全局项目 ──
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "执行结果"
        headers = [
            "目录层级",
            "目录名称",
            "意图ID",
            "意图标题",
            "预置条件",
            "意图动作",
            "预期结果",
            "意图类型",
            "风险等级",
            "自动化标记",
            "门槛标记",
            "意图建模作者",
            "创建时间",
            "意图验证结果",
            "执行时间",
            "API校验",
            "DB校验",
            "综合校验结果",
            "失败原因分析",
            "缺陷截图",
        ]
        header_fill = PatternFill(
            start_color="1A2D5A", end_color="1A2D5A", fill_type="solid"
        )
        header_font = Font(color="FFFFFF", bold=True, size=11, name="微软雅黑")
        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )

        dir_fill = PatternFill(
            start_color="E8ECF0", end_color="E8ECF0", fill_type="solid"
        )
        dir_font = Font(bold=True, size=11, name="微软雅黑", color="374151")
        fail_fill = PatternFill(
            start_color="FFF2F0", end_color="FFF2F0", fill_type="solid"
        )

        for ri, intent in enumerate(intents):
            row = ri + 2
            cid = intent.get("case_id", "").strip()
            title = intent.get("title", "")

            if not cid:
                ws.cell(
                    row=row, column=1, value=intent.get("catalog_level", "")
                ).font = dir_font
                ws.cell(
                    row=row, column=2, value=intent.get("catalog_name", "")
                ).font = dir_font
                for ci in range(1, len(headers) + 1):
                    ws.cell(row=row, column=ci).fill = dir_fill
                continue

            exec_rec = exec_records.get(cid, {})

            ws.cell(row=row, column=1, value=intent.get("catalog_level", ""))
            ws.cell(row=row, column=2, value="")
            ws.cell(row=row, column=3, value=cid)
            ws.cell(row=row, column=4, value=title)
            ws.cell(
                row=row, column=5, value=str(intent.get("precondition", ""))[:32767]
            )
            ws.cell(
                row=row,
                column=6,
                value=str(
                    intent.get("intent_summary", "")
                    or intent.get("case_actions_json", "")
                )[:32767],
            )
            ws.cell(
                row=row, column=7, value=str(intent.get("expected_outcome", ""))[:32767]
            )
            ws.cell(row=row, column=8, value=intent.get("case_type", ""))
            ws.cell(row=row, column=9, value=intent.get("risk_level", ""))
            ws.cell(row=row, column=10, value=intent.get("auto_flag", ""))
            ws.cell(row=row, column=11, value=intent.get("threshold_flag", ""))
            ws.cell(row=row, column=12, value=intent.get("author", ""))
            ws.cell(row=row, column=13, value=intent.get("created_date", ""))
            verification_status = exec_rec.get("status") or intent.get(
                "verification_status", ""
            )
            ws.cell(row=row, column=14, value=verification_status or "未执行")
            ws.cell(
                row=row,
                column=15,
                value=exec_rec.get("executed_at")
                or intent.get("verification_time", ""),
            )

            step_checks = intent.get("_step_checks", {})
            api_parts = []
            db_parts = []
            for s_idx, checks in sorted(step_checks.items()):
                ac = checks.get("api_checks", [])
                dc = checks.get("db_checks", [])
                if ac:
                    api_parts.append(
                        "步骤%s: %s" % (int(s_idx) + 1, "; ".join(str(x) for x in ac))
                    )
                if dc:
                    db_parts.append(
                        "步骤%s: %s" % (int(s_idx) + 1, "; ".join(str(x) for x in dc))
                    )
            ws.cell(
                row=row, column=16, value="\n".join(api_parts) if api_parts else "—"
            )
            ws.cell(row=row, column=17, value="\n".join(db_parts) if db_parts else "—")
            raw_status = str(verification_status).strip().upper()
            if not raw_status:
                combined = "未执行"
            elif raw_status == "PASS":
                combined = (
                    "全部通过"
                    if not (api_parts or db_parts)
                    else "通过（%d条API + %d条DB）"
                    % (sum(1 for _ in api_parts), sum(1 for _ in db_parts))
                )
            elif raw_status == "FAIL":
                combined = "失败（%d条API + %d条DB校验）" % (
                    sum(1 for _ in api_parts),
                    sum(1 for _ in db_parts),
                )
            else:
                combined = raw_status
            ws.cell(row=row, column=18, value=combined)

            failure_reason = exec_rec.get("failure_reason", "")
            if not failure_reason and raw_status == "FAIL":
                failure_reason = ExcelHandler._auto_failure_analysis(cid, title)
            ws.cell(
                row=row,
                column=19,
                value=str(failure_reason)[:32767] if failure_reason else "",
            )

            if cid in collected_ss:
                fname = ss_links.get(cid, "screenshots/fail_%s.png" % cid)
                link_cell = ws.cell(row=row, column=20, value="📎 截图(%s).png" % cid)
                link_cell.hyperlink = fname
                link_cell.font = Font(
                    color="0563C1", underline="single", name="微软雅黑"
                )
            elif raw_status == "FAIL":
                ws.cell(row=row, column=20, value="(无截图)")
            else:
                ws.cell(row=row, column=20, value="—")

            if raw_status == "FAIL":
                for ci in range(1, len(headers) + 1):
                    ws.cell(row=row, column=ci).fill = fail_fill

        col_widths = [
            8,
            14,
            14,
            22,
            20,
            35,
            30,
            8,
            8,
            8,
            8,
            10,
            14,
            10,
            14,
            25,
            25,
            22,
            40,
            22,
        ]
        for ci, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.freeze_panes = "A2"

        # ★ 纯内存：xlsx 写 BytesIO，zip 写 BytesIO，collected_ss 已是 MySQL bytes，全程不落盘
        xlsx_bio = BytesIO()
        wb.save(xlsx_bio)
        wb.close()
        zip_bio = BytesIO()
        with zipfile.ZipFile(zip_bio, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("执行结果.xlsx", xlsx_bio.getvalue())
            for cid, png_bytes in collected_ss.items():
                arc_name = "screenshots/fail_%s.png" % cid
                zf.writestr(arc_name, bytes(png_bytes))
        zip_bio.seek(0)

        dl_name = "执行结果_%s.zip" % (
            project_name if not executor else "%s_%s" % (project_name, executor)
        )
        return send_file(
            zip_bio,
            as_attachment=True,
            download_name=dl_name,
            mimetype="application/zip",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
