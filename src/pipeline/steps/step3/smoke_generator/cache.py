# -*- coding: utf-8 -*-
from __future__ import annotations

"""冒烟用例 Excel 的 MySQL 缓存读写 — 从 smoke_generator.py 拆出，零逻辑变更。"""

import base64
import logging
from typing import Any

_log = logging.getLogger(__name__)


def _write_smoke_excel_to_mysql(
    project_name: str, version_label: str, excel_bytes: bytes
) -> bool:
    """将冒烟用例 Excel 写入 MySQL source_documents(doc_type='smoke_excel')。"""
    import hashlib
    from src.core.db_manager import get_connection, release_connection

    encoded = base64.b64encode(excel_bytes).decode("ascii")
    content = (
        "## 冒烟用例 Excel（二进制 Base64）\n\n"
        "- project: %s\n"
        "- version_label: %s\n"
        "- byte_size: %d\n"
        "- base64_size: %d\n\n"
        "```smoke-excel-b64\n%s\n```\n"
    ) % (project_name, version_label, len(excel_bytes), len(encoded), encoded)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO source_documents "
            "(project, doc_type, version_label, content, content_hash, char_count, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW()) "
            "ON DUPLICATE KEY UPDATE content = VALUES(content), "
            "content_hash = VALUES(content_hash), char_count = VALUES(char_count), "
            "updated_at = NOW()",
            (
                project_name,
                "smoke_excel",
                version_label,
                content,
                content_hash,
                len(content),
            ),
        )
        conn.commit()
        _log.info(
            "    ✅ 冒烟用例 Excel 已写入 MySQL (doc_type=smoke_excel, version=%s, bytes=%d)",
            version_label,
            len(excel_bytes),
        )
        return True
    except Exception as _e:
        _log.error("    [WARN] 冒烟用例 Excel MySQL 写入失败: %s", _e)
        return False
    finally:
        release_connection(conn)


def _get_cached_smoke_excel(project_name: str, version_label: str) -> bytes | None:
    """从 MySQL 查询已缓存的冒烟用例 Excel（精确版本匹配）。"""
    from src.core.db_manager import get_connection, release_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT content FROM source_documents "
            "WHERE project = %s AND doc_type = 'smoke_excel' AND version_label = %s "
            "LIMIT 1",
            (project_name, version_label),
        ).fetchone()
        if row and row.get("content"):
            content = row["content"]
            import re

            m = re.search(r"```smoke-excel-b64\n(.+?)\n```", content, re.DOTALL)
            if m:
                return base64.b64decode(m.group(1))
        return None
    except Exception as _e:
        _log.warning("  [smoke] 查询缓存失败: %s", _e)
        return None
    finally:
        release_connection(conn)


def _get_latest_cached_smoke_excel(project_name: str) -> tuple[bytes, str] | None:
    """从 MySQL 查询最新缓存的冒烟用例 Excel（按项目名匹配，返回最新的）。"""
    from src.core.db_manager import get_connection, release_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT content, version_label FROM source_documents "
            "WHERE project = %s AND doc_type = 'smoke_excel' "
            "ORDER BY updated_at DESC LIMIT 1",
            (project_name,),
        ).fetchone()
        if row and row.get("content"):
            content = row["content"]
            import re

            m = re.search(r"```smoke-excel-b64\n(.+?)\n```", content, re.DOTALL)
            if m:
                return (base64.b64decode(m.group(1)), row.get("version_label", ""))
        return None
    except Exception as _e:
        _log.warning("  [smoke] 查询最新缓存失败: %s", _e)
        return None
    finally:
        release_connection(conn)


def _delete_cached_smoke_excel(project_name: str) -> bool:
    """清除 MySQL 中冒烟用例 Excel 缓存。"""
    from src.core.db_manager import get_connection, release_connection

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM source_documents WHERE project = %s AND doc_type = 'smoke_excel'",
            (project_name,),
        )
        conn.commit()
        _log.info("  [smoke] 已清除冒烟用例 Excel 缓存: project=%s", project_name)
        return True
    except Exception as _e:
        _log.warning("  [smoke] 清除缓存失败: %s", _e)
        return False
    finally:
        release_connection(conn)


# ★ SUP-009: 自动清理旧冒烟文件，仅保留最近 3 个
def _cleanup_old_smoke_excel(project_name: str, keep_count: int = 3) -> int:
    """删除旧冒烟 Excel 缓存，仅保留最新 keep_count 个。返回删除数量。"""
    from src.core.db_manager import get_connection, release_connection

    conn = None
    deleted = 0
    try:
        conn = get_connection()
        # 先查出所有 id，按更新时间倒序
        rows = conn.execute(
            "SELECT id FROM source_documents "
            "WHERE project = %s AND doc_type = 'smoke_excel' "
            "ORDER BY updated_at DESC",
            (project_name,),
        ).fetchall()
        if len(rows) > keep_count:
            ids_to_delete = [r["id"] for r in rows[keep_count:]]
            conn.execute(
                "DELETE FROM source_documents WHERE id IN (%s)"
                % ",".join(["%s"] * len(ids_to_delete)),
                tuple(ids_to_delete),
            )
            conn.commit()
            deleted = len(ids_to_delete)
            _log.info(
                "  [smoke] 自动清理 %d 个旧冒烟文件，保留 %d 个", deleted, keep_count
            )
    except Exception as _e:
        _log.warning("  [smoke] 自动清理旧文件失败: %s", _e)
    finally:
        release_connection(conn)
    return deleted


def _load_docs_smoke_excel_files(project_name: str) -> list[dict[str, Any]]:
    """查询 MySQL 中已缓存的冒烟用例 Excel 文件列表。"""
    import base64
    from src.core.db_manager import get_connection, release_connection

    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT version_label, char_count, updated_at FROM source_documents "
            "WHERE project = %s AND doc_type = 'smoke_excel' "
            "ORDER BY updated_at DESC",
            (project_name,),
        ).fetchall()
        files = []
        for r in rows:
            # 从 char_count 估算 byte_size（base64 膨胀约 1.37x，实际 Excel 约为 char_count * 0.25）
            est_size = max(int(r.get("char_count", 0) * 0.25), 0)
            files.append(
                {
                    "name": "冒烟用例_%s.xlsx" % (r.get("version_label", "unknown")),
                    "version_label": r.get("version_label", ""),
                    "size": est_size,
                    "mtime": (
                        str(r.get("updated_at", ""))[:19] if r.get("updated_at") else ""
                    ),
                }
            )
        return files
    except Exception as _e:
        _log.warning("  [smoke] 查询文件列表失败: %s", _e)
        return []
    finally:
        release_connection(conn)
