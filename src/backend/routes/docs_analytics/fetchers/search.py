# -*- coding: utf-8 -*-
"""
docs_analytics.fetchers.search — 文件搜索/扫描/路径/版本辅助函数
================================================================

包含：
- _FILE_ICONS / _PREVIEWABLE / _EDITABLE  — 文件常量
- _scan_dir               — 递归扫描目录返回文件树
- _safe_path              — 路径安全校验（防穿越）
- _infer_doc_type         — 从相对路径推断 source_documents.doc_type
- _auto_version_mysql     — 自动记录文档版本到 document_versions 表
"""

from datetime import datetime
from src.core.guarded import guard
from pathlib import Path
import logging

_log = logging.getLogger(__name__)

# ============================================================================
# 文件预览面板常量
# ============================================================================

_FILE_ICONS = {
    ".md": "📄",
    ".json": "📋",
    ".xlsx": "📊",
    ".xmind": "🧠",
    ".zip": "📦",
    ".png": "🖼️",
    ".txt": "📝",
    ".csv": "📈",
}

_PREVIEWABLE = {".md", ".json", ".xlsx", ".xmind"}
_EDITABLE = {".md", ".json"}


# ============================================================================
# 目录扫描
# ============================================================================


def _scan_dir(d, base):
    """递归扫描目录，返回文件树。"""
    items = []
    try:
        for entry in sorted(
            d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
        ):
            if entry.name.startswith(".") or entry.name.startswith("~$"):
                continue
            rel = str(entry.relative_to(base)).replace("\\", "/")
            if entry.is_dir():
                children = _scan_dir(entry, base)
                if children:
                    items.append(
                        {
                            "name": entry.name,
                            "path": rel,
                            "type": "dir",
                            "children": children,
                        }
                    )
            else:
                ext = entry.suffix.lower()
                size = entry.stat().st_size
                mtime = datetime.fromtimestamp(entry.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                items.append(
                    {
                        "name": entry.name,
                        "path": rel,
                        "type": "file",
                        "ext": ext,
                        "size": size,
                        "mtime": mtime,
                        "icon": _FILE_ICONS.get(ext, "📎"),
                        "previewable": ext in _PREVIEWABLE,
                        "editable": ext in _EDITABLE,
                    }
                )
    except (PermissionError, OSError) as e:
        _log.debug("[docs/files] 目录扫描跳过: %s", e)
    return items


# ============================================================================
# 路径安全校验
# ============================================================================


def _safe_path(rel, root):
    """路径安全校验：防穿越 + 统一分隔符。返回 resolved Path 或 None。"""
    import urllib.parse

    rel = urllib.parse.unquote(str(rel)).replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return None
    fp = (root / rel).resolve()
    if not str(fp).startswith(str(root.resolve())):
        return None
    return fp


# ============================================================================
# 文档类型推断
# ============================================================================


def _infer_doc_type(rel_path: str) -> str:
    """从相对路径推断 source_documents.doc_type。"""
    # ★ mysql:// URI 优先：mysql_doc.doc_type_from_mysql_uri 直接提取
    from src.backend.routes.docs_analytics.fetchers.mysql_doc import (
        doc_type_from_mysql_uri,
    )

    _uri_dt = doc_type_from_mysql_uri(rel_path)
    if _uri_dt:
        return _uri_dt
    p = rel_path.replace("\\", "/").lower()
    if "需求文档" in p or "confluence" in p or "requirement" in p:
        return "requirement"
    if "接口文档" in p or "yapi" in p:
        return "yapi"
    if "数据库" in p or "db" in p or "schema" in p:
        return "db_schema"
    if "接口意图" in p or "intent" in p:
        return "test_intents"
    if "设计稿" in p or "figma" in p:
        return "figma"
    if "需求拆解" in p or "breakdown" in p:
        return "breakdown"
    # fallback: 文件名（去扩展名）
    return Path(rel_path).stem


# ============================================================================
# 文档版本管理
# ============================================================================


def _auto_version_mysql(project: str, doc_type: str, old_hash: str, new_size: int):
    """自动记录文档版本到 document_versions 表（MySQL 化）。"""
    if not old_hash:
        return
    with guard("_auto_version_mysql: from src.users.manager import get_do", log=_log, level=logging.WARNING):
        from src.users.manager import get_document_versions, record_document_version

        versions = get_document_versions(project, doc_type, limit=1)
        last_hash = versions[0]["file_hash"] if versions else ""
        if old_hash == last_hash:
            return
        record_document_version(
            project,
            doc_type,
            f"mysql://source_documents/{doc_type}",
            source_hash=old_hash,
            size_bytes=new_size,
        )
