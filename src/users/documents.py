# -*- coding: utf-8 -*-
from __future__ import annotations

"""文档版本与截图管理模块"""
from datetime import datetime

from src.users._user_db_core import _conn


# 文档版本管理


def record_document_version(
    project: str,
    doc_type: str,
    file_path: str,
    source_hash: str = "",
    size_bytes: int = 0,
    created_by: str = "",
):
    """记录文档版本。version_id 基于时间戳自动生成。

    Args:
        created_by: 创建者用户ID，默认为空字符串
    """
    now = datetime.now()
    c = None
    try:
        c = _conn()
        c.execute(
            "REPLACE INTO document_versions(project, doc_type, version, file_path, file_hash, file_size, created_by, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                project,
                doc_type,
                now.strftime("%Y%m%d_%H%M%S"),
                file_path,
                source_hash,
                size_bytes,
                created_by,
                now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        c.commit()
        return now.strftime("%Y%m%d_%H%M%S")

    finally:
        if c:
            c.close()


def get_document_versions(project: str, doc_type: str, limit: int = 30):
    """查询文档版本列表。"""
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT version, file_path, file_hash, file_size, created_at "
            "FROM document_versions WHERE project=%s AND doc_type=%s ORDER BY id DESC LIMIT %s",
            (project, doc_type, limit),
        ).fetchall()
        # ★ DictCursor 返回 dict 行，用列名访问（原 r[0] 整数索引会 KeyError）
        return [
            {
                "version": r["version"],
                "file_path": r["file_path"],
                "file_hash": r["file_hash"],
                "file_size": r["file_size"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # 原始文档内容管理
    finally:
        if c:
            c.close()


def record_source_document(
    project: str,
    doc_type: str,
    version_label: str,
    content: str,
    content_hash: str = "",
    char_count: int = 0,
):
    """写入原始文档内容到 source_documents 表。幂等覆盖。"""
    if not content:
        return
    c = None
    try:
        c = _conn()
        c.execute(
            "REPLACE INTO source_documents(project, doc_type, version_label, content, content_hash, char_count) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (
                project,
                doc_type,
                version_label,
                content,
                content_hash,
                char_count or len(content),
            ),
        )
        c.commit()

    finally:
        if c:
            c.close()


def get_source_document(
    project: str, doc_type: str, version_label: str = ""
) -> str | None:
    """读取原始文档内容。version_label 为空时返回最新一条。"""
    c = None
    try:
        c = _conn()
        if version_label:
            row = c.execute(
                "SELECT content FROM source_documents WHERE project=%s AND doc_type=%s AND version_label=%s",
                (project, doc_type, version_label),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT content FROM source_documents WHERE project=%s AND doc_type=%s ORDER BY created_at DESC LIMIT 1",
                (project, doc_type),
            ).fetchone()
        return row["content"] if row else None

    # 截图管理
    finally:
        if c:
            c.close()


def save_screenshot(
    project: str, case_id: str, run_id: str, step_num: int, image_data: bytes
) -> int:
    """截图写入 execution_screenshots 表。返回记录 ID。"""
    if not image_data:
        return 0
    c = None
    try:
        c = _conn()
        cur = c.execute(
            "INSERT INTO execution_screenshots(project, case_id, run_id, step_num, image_data, file_size) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (project, case_id, run_id, step_num, image_data, len(image_data)),
        )
        c.commit()
        return cur.lastrowid

    finally:
        if c:
            c.close()


def get_screenshot(screenshot_id: int) -> dict | None:
    """按 ID 读取截图二进制和元信息。"""
    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT id, project, case_id, run_id, step_num, image_data, file_size, created_at "
            "FROM execution_screenshots WHERE id=%s",
            (screenshot_id,),
        ).fetchone()
        return row if row else None

    finally:
        if c:
            c.close()


def get_screenshots_by_intent(
    project: str, case_id: str, limit: int = 10
) -> list[dict]:
    """按测试用例查询截图列表（不含二进制，仅元信息）。"""
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, project, case_id, run_id, step_num, file_size, created_at "
            "FROM execution_screenshots WHERE project=%s AND case_id=%s ORDER BY id DESC LIMIT %s",
            (project, case_id, limit),
        ).fetchall()
        return rows

    # 操作指令缓存已迁移到 step4_op_cache 表（op_cache.py 直接管理），
    #   旧的 get_op_cache / set_op_cache / delete_op_cache 均已移除（无调用方）。

    finally:
        if c:
            c.close()
