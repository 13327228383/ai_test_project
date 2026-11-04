# -*- coding: utf-8 -*-
"""
file_upload package — 上传路由
"""

import os
from src.core.guarded import guard
import hashlib
import logging
import tempfile
from pathlib import Path
from flask import request, jsonify

from . import file_upload_bp
from ._decorators import (
    _login_required,
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    FORMAT_CATEGORY_MAP,
    FILE_CATEGORIES,
    _allowed_file,
)

_log = logging.getLogger(__name__)


@file_upload_bp.route("/api/step1/upload", methods=["POST"])
@_login_required
def upload_requirement_file():
    """
    上传需求文档文件 — ★ P2: 只提取数据，不保存物理文件。

    Form 参数:
        - file: 文件对象
        - project: 项目名称（可选，默认从配置读取）
        - weight: 权重 0-1（可选，默认 0.5）
        - description: 文件描述（可选）

    返回:
        {"success": bool, "file_id": str, "text_length": int, "format": str}
    """
    from src.core.config_loader import config

    if "file" not in request.files:
        return jsonify({"ok": False, "success": False, "error": "缺少文件字段"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"ok": False, "success": False, "error": "未选择文件"}), 400

    if not _allowed_file(file.filename):
        return (
            jsonify(
                {
                    "ok": False,
                    "success": False,
                    "error": "不支持的文件格式: %s。支持: %s"
                    % (Path(file.filename).suffix, ", ".join(ALLOWED_EXTENSIONS)),
                }
            ),
            400,
        )

    # 获取参数
    project_name = (request.form.get("project") or config.project_name or "").strip()
    if not project_name:
        return (
            jsonify(
                {
                    "ok": False,
                    "success": False,
                    "error": "未选择项目，请先在顶部选择项目后再上传",
                }
            ),
            400,
        )
    weight = float(request.form.get("weight", 0.5))
    weight = max(0.0, min(1.0, weight))  # 限制在 0-1
    description = request.form.get("description", "")
    # ★ P1: 分类参数（手动指定或自动推断）
    category = request.form.get("category", "").strip()

    # ★ 权限校验：仅项目管理员（file.upload）可上传文件，项目成员无权
    from src.models.permission import check_admin_or_permission

    ok, err = check_admin_or_permission("file.upload", project_name)
    if not ok:
        return jsonify({"ok": False, "success": False, "error": err}), 403

    # ★ 白名单校验：category 必须为已知分类
    ALLOWED_CATEGORIES = set(FILE_CATEGORIES.keys())
    if category and category not in ALLOWED_CATEGORIES:
        return (
            jsonify(
                {
                    "ok": False,
                    "success": False,
                    "error": "无效的文件分类: %s" % category,
                }
            ),
            400,
        )

    # ★ P2: 使用内存/临时文件解析，不持久化保存
    # 保留原始文件名用于显示（不落盘，无路径遍历风险）
    original_filename = file.filename or "unknown"
    ext = Path(original_filename).suffix.lower()

    try:
        # 先检查文件大小（避免将大文件全部读入内存）
        content_length = request.content_length or file.content_length
        if content_length and content_length > MAX_FILE_SIZE:
            return (
                jsonify({"ok": False, "success": False, "error": "文件超过50MB限制"}),
                400,
            )

        # 读取文件内容到内存
        file_content = file.read()
        if len(file_content) > MAX_FILE_SIZE:
            return (
                jsonify({"ok": False, "success": False, "error": "文件超过50MB限制"}),
                400,
            )

        # 写入临时文件供解析器使用
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        # 解析文件
        from src.pipeline.steps.step1.file_parser import FileParser

        parse_result = FileParser.parse(tmp_path)

        # 立即删除临时文件
        with guard("upload_requirement_file: os.unlink(tmp_path)", log=_log, level=logging.WARNING):
            os.unlink(tmp_path)

        if not parse_result.get("success"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "success": False,
                        "error": "文件解析失败（%s）: %s"
                        % (
                            original_filename,
                            parse_result.get("error", "未知错误"),
                        ),
                    }
                ),
                400,
            )

        text = parse_result.get("text", "")
        file_format = parse_result.get("format", "")
        metadata = parse_result.get("metadata", {})
        # 空文本已由 FileParser 入口统一判失败（success=False），此处不再重复判定

        # 计算哈希
        file_hash = hashlib.sha256(text.encode()).hexdigest()

        # ★ P1: 自动推断分类（如果用户没有手动指定）
        if not category:
            category = FORMAT_CATEGORY_MAP.get(ext, "requirement")

        # ★ P2: 只存入 MySQL，不保存物理文件
        _save_to_database(
            project_name,
            original_filename,
            file_format,
            weight,
            file_hash,
            len(text),
            text,
            category,
        )

        return jsonify(
            {
                "ok": True,
                "success": True,
                "file_id": file_hash[:16],
                "filename": original_filename,
                "format": file_format,
                "text_length": len(text),
                "weight": weight,
                "category": category,
                "category_label": FILE_CATEGORIES.get(category, category),
                "metadata": metadata,
            }
        )

    except Exception as e:
        _log.error("文件上传处理异常: %s", e, exc_info=True)
        return (
            jsonify(
                {"ok": False, "success": False, "error": "服务器内部错误，请稍后重试"}
            ),
            500,
        )


def _save_to_database(
    project_name: str,
    filename: str,
    file_format: str,
    weight: float,
    content_hash: str,
    content_length: int,
    content: str,
    category: str = "requirement",
):
    """★ 统一写入 source_documents（含上传元数据字段）。

    行身份 = doc_type（upload_{format}_{hash8}），因此"同一份文件改了内容"会得到
    新的 doc_type。这里按「项目 + 版本 + 分类 + 文件名」supersede：写入新行后删除
    同标识的旧行，否则旧文档会一直留在索引/审计里，被当成另一份需求重复计入。
    """
    from src.core.db_manager import get_connection
    from src.core.versioning import current_version_label

    doc_type = "upload_%s_%s" % (file_format, content_hash[:8])
    version_label = current_version_label(project_name) or "v1.0"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO source_documents
            (project, doc_type, version_label, content, content_hash, char_count,
             filename, file_format, weight, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content = VALUES(content),
                content_hash = VALUES(content_hash),
                char_count = VALUES(char_count),
                filename = VALUES(filename),
                file_format = VALUES(file_format),
                weight = VALUES(weight),
                category = VALUES(category),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                project_name,
                doc_type,
                version_label,
                content,
                content_hash,
                content_length,
                filename,
                file_format,
                weight,
                category,
            ),
        )
        conn.execute(
            "DELETE FROM source_documents WHERE project = %s AND version_label = %s "
            "AND category = %s AND filename = %s AND doc_type <> %s",
            (project_name, version_label, category, filename, doc_type),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
