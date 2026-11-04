# -*- coding: utf-8 -*-
"""
文档中心 — 冒烟用例导出路由
============================
冒烟用例 Excel 生成 + 后台线程状态 + 缓存检查 + 下载 + 清除。
从 docs_core.py 拆分出来。

流程:
  1. 用户点击下载 → 检查 MySQL 缓存
  2. 有缓存 → 直接返回 Excel
  3. 无缓存 → 后台 AI 生成 → 写入 MySQL → 返回 Excel
"""

import io
import threading
from flask import jsonify, request, send_file
import logging

_log = logging.getLogger(__name__)

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


# ══════════════════════════════════════════════════════════
# 冒烟用例生成 — 共享状态
# ══════════════════════════════════════════════════════════

# ★ SUP-006: 按项目隔离的冒烟生成状态（避免全局串台）
_smoke_gen_lock = threading.Lock()
_smoke_gen_running: dict = {}  # project_name → bool
_smoke_gen_result: dict = {}  # project_name → dict


# ══════════════════════════════════════════════════════════
# 冒烟用例文件列表（从 MySQL 查询）
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/smoke-intent-files")
def api_docs_smoke_case_asset_files():
    """返回冒烟用例 Excel 文件列表（从 MySQL source_documents 查询）。"""
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"files": []}), (401 if err.startswith("请先登录") else 403)
    if not _current_project():
        return jsonify({"files": []})
    try:
        from src.pipeline.steps.step3.smoke_generator import (
            _load_docs_smoke_excel_files,
        )

        files = _load_docs_smoke_excel_files(_current_project())
        return jsonify({"files": files})
    except Exception as e:
        _log.warning("[smoke-files] 查询失败: %s", e)
        return jsonify({"files": []})


# ══════════════════════════════════════════════════════════
# 冒烟用例生成（异步后台执行）
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/generate-smoke", methods=["POST"])
def api_docs_generate_smoke():
    """触发冒烟用例 Excel 生成（异步后台执行）。立即返回，前端轮询 /api/docs/generate-smoke/status 获取进度。"""
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    data = request.get_json(silent=True) or {}
    req_project = (data.get("project") or "").strip() if isinstance(data, dict) else ""
    project, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status

    with _smoke_gen_lock:
        if _smoke_gen_running.get(project, False):
            return jsonify(
                {"started": True, "running": True, "hint": "冒烟用例正在生成中..."}
            )
        _smoke_gen_running[project] = True
        _smoke_gen_result[project] = {
            "count": 0,
            "name": "",
            "size": 0,
            "done": False,
            "error": "",
            "version_label": "",
        }

    # 捕获当前线程的 session 上下文
    from src.core.config_loader import get_current_user, get_session_key

    uid = get_current_user()
    session_key = get_session_key()

    def _bg_generate():
        try:
            from src.core.config_loader import set_current_user, set_session_key

            set_current_user(uid)
            if session_key:
                set_session_key(session_key)

            from src.backend.helpers import _temporary_project
            from src.pipeline.steps.step3.smoke_generator import generate_smoke_excel

            # ★ 后台线程需临时切换全局项目，确保 excel_handler 读到目标项目的需求数据
            with _temporary_project(project):
                excel_bytes, version_label, case_count = generate_smoke_excel(
                    project_name=project, force=True
                )

            with _smoke_gen_lock:
                if excel_bytes and case_count > 0:
                    _smoke_gen_result[project] = {
                        "count": case_count,
                        "done": True,
                        "name": "冒烟用例_%s.xlsx" % version_label,
                        "size": len(excel_bytes),
                        "error": "",
                        "version_label": version_label,
                    }
                    _log.info(
                        "##HTML:##<span style='color:#4ADE71'>🔥 冒烟用例 Excel 已生成: %d 条 → %s (%d bytes)</span>",
                        case_count,
                        version_label,
                        len(excel_bytes),
                    )
                elif case_count == -1:
                    _smoke_gen_result[project] = {
                        "count": case_count,
                        "done": True,
                        "name": "冒烟用例_%s.xlsx" % version_label,
                        "size": len(excel_bytes) if excel_bytes else 0,
                        "error": "",
                        "version_label": version_label,
                    }
                    _log.info(
                        "##HTML:##<span style='color:#4ADE71'>🔥 冒烟用例 Excel 命中缓存: %s</span>",
                        version_label,
                    )
                else:
                    _smoke_gen_result[project] = {
                        "count": 0,
                        "name": "",
                        "size": 0,
                        "done": True,
                        "error": "未生成有效用例",
                        "version_label": "",
                    }
                    _log.info(
                        "##HTML:##<span style='color:#F59E0B'>🔥 冒烟用例: 未生成有效用例</span>"
                    )
        except Exception as e:
            import traceback

            traceback.print_exc()
            with _smoke_gen_lock:
                _smoke_gen_result[project] = {
                    "count": 0,
                    "name": "",
                    "size": 0,
                    "done": True,
                    "error": str(e),
                    "version_label": "",
                }
            _log.info(
                "##HTML:##<span style='color:#EF4444'>🔥 冒烟用例异常: %s</span>",
                str(e)[:100],
            )
        finally:
            with _smoke_gen_lock:
                _smoke_gen_running[project] = False

    t = threading.Thread(target=_bg_generate, daemon=True)
    t.start()
    _log.info(
        "##HTML:##<span style='color:#F59E0B'>🔥 冒烟用例: 后台生成已启动...</span>"
    )
    return jsonify(
        {"started": True, "running": True, "hint": "冒烟用例正在后台生成..."}
    )


@docs_bp.route("/api/docs/generate-smoke/status")
def api_docs_generate_smoke_status():
    """查询冒烟用例 Excel 生成进度。支持 ?project=xxx 指定项目。"""
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    req_project = request.args.get("project", "").strip()
    project, err = _resolve_request_project(req_project)
    if err:
        # ★ 轮询接口失败时返回未完成状态，前端继续轮询（不报错，避免弹窗干扰）
        return jsonify(
            {
                "count": 0,
                "name": "",
                "size": 0,
                "done": False,
                "error": "",
                "version_label": "",
            }
        )
    with _smoke_gen_lock:
        result = dict(
            _smoke_gen_result.get(
                project,
                {
                    "count": 0,
                    "name": "",
                    "size": 0,
                    "done": False,
                    "error": "",
                    "version_label": "",
                },
            )
        )
    return jsonify(result)


# ══════════════════════════════════════════════════════════
# 冒烟用例下载 / 状态检查
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/smoke-intents")
def api_docs_smoke_intents():
    """
    下载冒烟用例 Excel。
    流程：检查 MySQL 缓存 → 有则直接返回 → 无则同步生成并返回。
    支持 ?check=1 检查最新文件状态。
    支持 ?version=xxx 指定版本。
    支持 ?project=xxx 指定项目（admin 可下载任意可见项目，无需先切换当前项目）。
    """
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

    check_only = request.args.get("check", "") == "1"

    try:
        from src.pipeline.steps.step3.smoke_generator import (
            generate_smoke_excel,
            _get_latest_cached_smoke_excel,
            _load_docs_smoke_excel_files,
        )

        if check_only:
            files = _load_docs_smoke_excel_files(project_name)
            if files:
                latest = files[0]
                return jsonify(
                    {"exists": True, "name": latest["name"], "size": latest["size"]}
                )
            return jsonify({"exists": False})

        # 尝试获取最新缓存（按项目名匹配，不要求精确版本）
        excel_bytes = None
        version_label = ""
        result = _get_latest_cached_smoke_excel(project_name)
        if result:
            excel_bytes, version_label = result

        if not excel_bytes:
            # 无缓存 → 同步生成（临时切换全局项目，确保读到目标项目的需求数据）
            _log.info("  [冒烟] 无缓存，开始同步生成...")
            with _temporary_project(project_name):
                excel_bytes, version_label, case_count = generate_smoke_excel(
                    project_name=project_name, force=True
                )

        if not excel_bytes:
            return (
                jsonify({"ok": False, "error": "冒烟用例文件尚未生成，请先生成"}),
                404,
            )

        dl_name = "冒烟用例_%s.xlsx" % (version_label or "smoke")
        return send_file(
            io.BytesIO(excel_bytes),
            as_attachment=True,
            download_name=dl_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════
# 冒烟用例缓存清除
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/smoke-intents/clear", methods=["POST"])
def api_docs_smoke_intents_clear():
    """清除冒烟用例 Excel 缓存（MySQL source_documents 中 doc_type='smoke_excel'）。"""
    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project, _temporary_project

    data = request.get_json(silent=True) or {}
    req_project = (data.get("project") or "").strip() if isinstance(data, dict) else ""
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status

    try:
        from src.pipeline.steps.step3.smoke_generator import clear_smoke_cache

        # ★ clear_smoke_cache 依赖全局项目，需临时切换
        with _temporary_project(project_name):
            success = clear_smoke_cache()
        if success:
            _log.info(
                "##HTML:##<span style='color:#4ADE71'>🗑️ 冒烟用例 Excel 缓存已清除</span>"
            )
            return jsonify({"ok": True, "message": "冒烟用例 Excel 缓存已清除"})
        else:
            return jsonify({"ok": True, "message": "无需清除（缓存不存在）"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
