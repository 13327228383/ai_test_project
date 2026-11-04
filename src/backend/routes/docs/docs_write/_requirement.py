# -*- coding: utf-8 -*-
"""
文档中心 — 需求稿上传路由
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)

from flask import jsonify, request

from src.backend.routes.docs import docs_bp
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


# ══════════════════════════════════════════════════════════
# 需求稿上传 (POST)
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/requirement", methods=["POST"])
def api_docs_requirement_upload():
    ok, err = check_admin_or_permission("docs.edit", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    data = request.get_json(silent=True) or {}
    req_project = (data.get("project") or "").strip() if isinstance(data, dict) else ""
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    from src.core.db_manager import get_connection

    content = ""
    if data and data.get("content"):
        content = data["content"]
    elif request.files and "file" in request.files:
        content = request.files["file"].read().decode("utf-8")
    else:
        return (
            jsonify({"ok": False, "error": "请提供 content（JSON）或上传 .md 文件"}),
            400,
        )
    if not content.strip():
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    # ★ 写入 MySQL source_documents(doc_type='confluence')，不再落盘
    from hashlib import md5

    from src.core.versioning import current_version_label

    # ★ 版本标签随当前 active version 动态解析，避免硬编码 'current' 导致 FK 落不到真实版本。
    #   查不到 active 时回退到项目名首版本标签（project_versions 最小 id），仍不写假标签。
    _ver_label = current_version_label(project_name) or ""
    if not _ver_label:
        _fc = get_connection()
        try:
            _row = _fc.execute(
                "SELECT version_label FROM project_versions "
                "WHERE project=%s ORDER BY id ASC LIMIT 1",
                (project_name,),
            ).fetchone()
            if _row:
                _ver_label = _row["version_label"]
        finally:
            _fc.close()
    _conn = get_connection()
    try:
        _ch = md5(content.encode("utf-8")).hexdigest()[:16]
        _conn.execute(
            "INSERT INTO source_documents (project, doc_type, version_label, content, "
            "content_hash, char_count, created_at, updated_at) VALUES "
            "(%s, %s, %s, %s, %s, %s, NOW(), NOW()) "
            "ON DUPLICATE KEY UPDATE content=%s, content_hash=%s, char_count=%s, updated_at=NOW()",
            (
                project_name,
                "confluence",
                _ver_label,
                content,
                _ch,
                len(content),
                content,
                _ch,
                len(content),
            ),
        )
        _conn.commit()
    finally:
        try:
            _conn.close()
        except Exception as _pass_e:
            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    _log.info(
        "##HTML:##<span style='color:#4ADE71'>📄 需求稿已写入 MySQL (%d 字符)，后续 Step 2 将使用新版本</span>",
        len(content),
    )
    return jsonify({"ok": True, "size": len(content)})


# ══════════════════════════════════════════════════════════
# 需求稿 AI 补全 (POST)
# ══════════════════════════════════════════════════════════


@docs_bp.route("/api/docs/requirement/ai-supplement", methods=["POST"])
def api_docs_requirement_ai_supplement():
    ok, err = check_admin_or_permission("docs.edit", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.helpers import _resolve_request_project

    data = request.get_json(silent=True) or {}
    req_project = (data.get("project") or "").strip() if isinstance(data, dict) else ""
    project_name, err = _resolve_request_project(req_project)
    if err:
        msg, status = err
        return jsonify({"ok": False, "error": msg}), status
    from src.core.db_manager import get_connection
    from src.ai.ai_client import ai_brain

    # ★ 从 MySQL source_documents 读取当前需求稿
    _conn = get_connection()
    current_text = ""
    with guard("api_docs_requirement_ai_supplement: _row = _conn.execute( 'SELECT conten", log=_log, level=logging.WARNING):
        _row = _conn.execute(
            "SELECT content FROM source_documents "
            "WHERE project = %s AND doc_type = 'confluence' LIMIT 1",
            (project_name,),
        ).fetchone()
        if _row and _row.get("content"):
            current_text = _row["content"]
    _conn.close()
    if not current_text.strip():
        return (
            jsonify({"ok": False, "error": "需求稿为空或不存在，请先执行 Step 1"}),
            400,
        )
    system_prompt = (
        "你是资深测试需求分析专家。请审查以下需求文档，找出可能遗漏的内容。\n\n"
        "## 检查维度\n"
        "1. 功能完整性 2. 边界条件 3. 角色权限 4. 数据依赖 5. 交互流程\n\n"
        "## 输出格式（严格 JSON）\n"
        '{"has_gaps": true/false,\n'
        ' "supplements": [{"section": "位置描述", "content": "补充内容...", "reason": "为什么需要补充"}, ...],\n'
        ' "summary": "一句话总结"}\n'
    )
    try:
        result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message="## 当前需求文档\n\n" + current_text[:8000],
            temperature=0.3,
            max_tokens=8192,  # ★ v10: 推理模型安全下限（原2048→Router下限8192兜底）
            caller_name="需求稿AI补全",
            task_type="requirement_analysis",
            force_cloud=True,
        )
        if not result.get("success"):
            return jsonify({"ok": False, "error": "AI 调用失败"}), 500
        parsed = ai_brain._parse_json_response(result["data"])
        if not parsed.get("success"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "AI 返回解析失败",
                        "raw": result["data"][:500],
                    }
                ),
                500,
            )
        return jsonify(parsed["data"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
