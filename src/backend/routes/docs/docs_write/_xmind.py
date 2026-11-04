# -*- coding: utf-8 -*-
"""
文档中心 — XMind 测试设计下载路由
"""

import base64
import io
import logging
import re

_log = logging.getLogger(__name__)

from flask import jsonify, request, send_file

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


@docs_bp.route("/api/docs/xmind-design", methods=["POST"])
def api_docs_xmind_design():
    """生成 XMind 测试设计文件并下载。复用 pipeline_step3 的统一生成逻辑。

    支持 body.project 指定项目（admin 可下载任意可见项目，无需先切换当前项目）。
    """
    ok, err = check_admin_or_permission("docs.view", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )

    from src.backend.helpers import _resolve_request_project, _temporary_project

    data = request.get_json(silent=True) or {}
    req_project = (data.get("project") or "").strip()
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status

    try:
        from src.pipeline.steps.step3.xmind_designer import generate_xmind_design

        # ★ generate_xmind_design + _read_xmind_from_db 均依赖全局 config.project_name，
        #   必须在临时项目上下文内完成"生成+读取"，否则读取时会用错项目名查不到数据。
        with _temporary_project(project_name):
            version_label = generate_xmind_design()
            if not version_label:
                return (
                    jsonify(
                        {"ok": False, "error": "XMind 生成失败，请查看后端日志了解详情"}
                    ),
                    500,
                )
            xmind_bytes = _read_xmind_from_db(version_label)
        if not xmind_bytes:
            return (
                jsonify({"ok": False, "error": "XMind 已生成但无法从数据库读取"}),
                500,
            )

        dl_name = version_label + ".xmind"
        _log.info(
            "##HTML:##<span style='color:#4ADE71'>🧠 XMind 测试设计已生成: %s (%d bytes)</span>",
            dl_name,
            len(xmind_bytes),
        )
        return send_file(
            io.BytesIO(xmind_bytes),
            as_attachment=True,
            download_name=dl_name,
            mimetype="application/x-xmind",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


def _read_xmind_from_db(version_label):
    """从 source_documents 表读取 XMind base64 内容并解码为二进制。"""
    conn = None
    try:
        from src.core.db_manager import get_connection
        from src.core.config_loader import config

        project_name = getattr(config, "project_name", "") or ""
        conn = get_connection()
        result = conn.execute(
            "SELECT content FROM source_documents "
            "WHERE project = %s AND doc_type = 'xmind' AND version_label = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (project_name, version_label),
        )
        row = result.fetchone()
        if not row:
            _log.error("[xmind] DB 未找到 xmind 记录: version_label=%s", version_label)
            return None

        content = row[0]
        # 提取 ```xmind-b64 ... ``` 中的 base64
        m = re.search(r"```xmind-b64\n(.*?)\n```", content, re.DOTALL)
        if not m:
            _log.error("[xmind] 无法从 content 中提取 xmind-b64 块")
            return None

        return base64.b64decode(m.group(1))
    except Exception as _e:
        _log.error("[xmind] DB 读取失败: %s", _e)
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("_xmind.py:close 异常: %s", _pass_e, exc_info=True)
