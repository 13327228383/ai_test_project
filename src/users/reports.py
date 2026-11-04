# -*- coding: utf-8 -*-
from __future__ import annotations

"""流水线生成报告管理 — 与 source_documents 分离，避免源文档与生成物混用。"""
import logging

from src.users._user_db_core import _conn

_log = logging.getLogger(__name__)


def save_generated_report(
    project: str,
    report_type: str,
    content: str,
    version_id: int = 0,
    version_label: str = "",
    run_id: str = "",
) -> bool:
    """写入生成报告到 generated_reports 表。幂等覆盖（按 project+version_id+run_id+report_type）。"""
    if not project or not report_type or not content:
        return False
    content_hash = ""
    try:
        import hashlib

        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()[:16]
    except Exception as _e:
        _log.debug("content_hash 计算失败: %s", _e, exc_info=True)
    c = None
    try:
        c = _conn()
        c.execute(
            """
            INSERT INTO generated_reports(
                project, version_id, version_label, run_id, report_type,
                content, content_hash, char_count, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                version_label = VALUES(version_label),
                content = VALUES(content),
                content_hash = VALUES(content_hash),
                char_count = VALUES(char_count),
                updated_at = NOW()
            """,
            (
                project,
                version_id,
                version_label,
                run_id,
                report_type,
                content,
                content_hash,
                len(content),
            ),
        )
        c.commit()
        return True
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[reports] save_generated_report 失败: %s", str(e)[:120]
        )
        return False
    finally:
        if c:
            c.close()


def get_generated_report(
    project: str,
    report_type: str,
    version_id: int = 0,
    run_id: str = "",
) -> str | None:
    """读取生成报告内容。version_id/run_id 为空时返回最新一条。"""
    if not project or not report_type:
        return None
    c = None
    try:
        c = _conn()
        if version_id or run_id:
            row = c.execute(
                "SELECT content FROM generated_reports "
                "WHERE project=%s AND report_type=%s AND version_id=%s AND run_id=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (project, report_type, version_id, run_id),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT content FROM generated_reports "
                "WHERE project=%s AND report_type=%s ORDER BY created_at DESC LIMIT 1",
                (project, report_type),
            ).fetchone()
        return row["content"] if row else None
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[reports] get_generated_report 失败: %s", str(e)[:120]
        )
        return None
    finally:
        if c:
            c.close()


def list_generated_reports(
    project: str,
    report_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """列出生成报告元信息（不含 content）。"""
    c = None
    try:
        c = _conn()
        if report_type:
            rows = c.execute(
                "SELECT id, version_id, version_label, run_id, report_type, content_hash, char_count, created_at "
                "FROM generated_reports WHERE project=%s AND report_type=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (project, report_type, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, version_id, version_label, run_id, report_type, content_hash, char_count, created_at "
                "FROM generated_reports WHERE project=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (project, limit),
            ).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[reports] list_generated_reports 失败: %s", str(e)[:120]
        )
        return []
    finally:
        if c:
            c.close()


def delete_generated_reports(
    project: str,
    report_type: str | None = None,
    version_id: int | None = None,
    run_id: str | None = None,
) -> bool:
    """按条件删除生成报告。"""
    c = None
    try:
        c = _conn()
        where = ["project=%s"]
        params: list = [project]
        if report_type:
            where.append("report_type=%s")
            params.append(report_type)
        if version_id is not None:
            where.append("version_id=%s")
            params.append(version_id)
        if run_id:
            where.append("run_id=%s")
            params.append(run_id)
        sql = "DELETE FROM generated_reports WHERE " + " AND ".join(where)
        c.execute(sql, params)
        c.commit()
        return True
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "[reports] delete_generated_reports 失败: %s", str(e)[:120]
        )
        return False
    finally:
        if c:
            c.close()
