# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Figma 图片导入工具 — 将 MCP download_figma_images 下载的图片转存 MySQL。

★ 全走MySQL零文件残留：扫描 MCP --image-dir 目录，将图片二进制写入
  figma_images 表，成功后删除磁盘文件。所有路径从 config.project_root 动态计算。

使用方式：
    from src.pipeline.utils.figma_image_store import import_figma_images
    # ★ 某SPA系统业务名已统一到 config.demo.sample_project
    stats = import_figma_images(project=DEMO_PROJECT, version_label="v1.0")
"""

import json as _json
import logging
import re
import struct as _struct
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 文件扩展名 → MIME 类型（稳定格式映射，无需配置化）
# ---------------------------------------------------------------------------
_EXT_MIME: dict[str, str] = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Figma node ID 正则：I1234:5678 或 1234:5678 格式
_FIGMA_NODE_ID_RE = re.compile(r"^I?\d+[:|-]\d+")

# 合法表名校验（仅允许 a-zA-Z0-9_，防止 SQL 注入）
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# 默认图片目录（相对于项目根目录，与 .mcp.json 中的 --image-dir 保持一致）
_DEFAULT_IMAGE_SUBDIR = Path("temp") / "figma_images"


def _resolve_image_dir(image_dir: Path | None = None) -> Path:
    """解析图片目录，优先参数，其次 config.project_root 拼默认子目录。"""
    if image_dir is not None:
        return Path(image_dir)
    try:
        from src.core.config_loader import config

        root = getattr(config, "project_root", None)
        if root:
            return Path(root) / _DEFAULT_IMAGE_SUBDIR
    except Exception as _e:
        _log.debug("[figma_image_store] _resolve_image_dir 异常: %s", _e, exc_info=True)
    # 兜底：相对于当前文件的项目根（仅 config 不可用时）
    return Path(__file__).resolve().parent.parent.parent.parent / _DEFAULT_IMAGE_SUBDIR


def _resolve_project(project: str = "") -> str:
    """解析项目名：参数 > config.project_name > 空字符串。"""
    if project:
        return project
    try:
        from src.core.config_loader import config

        name = getattr(config, "project_name", "") or ""
        if name:
            return name
    except Exception as _e:
        _log.debug("[figma_image_store] _resolve_project 异常: %s", _e, exc_info=True)
    return ""


def _extract_node_id(filename: str) -> str:
    """从文件名中提取 Figma node ID（如 1234:5678），失败则返回 stem。

    Figma MCP download_figma_images 生成的文件名通常包含 node ID，
    如 '1234_5678_menu_icon.png' 或 'I1234-5678.svg'。
    """
    stem = Path(filename).stem
    # 尝试匹配 Figma node ID 格式
    m = _FIGMA_NODE_ID_RE.search(filename)
    if m:
        return m.group(0)
    # 匹配下划线分隔的数字对：1234_5678
    m2 = re.search(r"(\d{4,})[_](\d{4,})", filename)
    if m2:
        return f"{m2.group(1)}:{m2.group(2)}"
    return stem


def _detect_image_type(file_path: Path) -> str:
    """通过扩展名检测图片类型。"""
    ext = file_path.suffix.lower()
    return ext.lstrip(".") if ext in _EXT_MIME else "png"


def _get_image_dimensions(data: bytes, image_type: str) -> tuple:
    """尝试获取 PNG/GIF 的宽高，SVG/JPEG 返回 (None, None)。"""
    if image_type in ("svg",):
        return None, None
    try:
        if image_type in ("png",) and len(data) >= 24 and data[1:4] == b"PNG":
            width = _struct.unpack(">I", data[16:20])[0]
            height = _struct.unpack(">I", data[20:24])[0]
            return width, height
        if image_type in ("gif",) and len(data) >= 10:
            width = _struct.unpack("<H", data[6:8])[0]
            height = _struct.unpack("<H", data[8:10])[0]
            return width, height
    except Exception as _e:
        _log.debug(
            "[figma_image_store] _get_image_dimensions 异常: %s", _e, exc_info=True
        )
    return None, None


def _collect_image_files(image_dir: Path) -> list[Path]:
    """扫描目录收集所有支持的图片文件。"""
    if not image_dir.exists():
        return []
    files: list[Path] = []
    for f in image_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in _EXT_MIME:
            files.append(f)
    return files


def _cleanup_empty_dirs(root: Path) -> None:
    """清理空子目录（自底向上，不删除 root 本身）。"""
    try:
        for d in sorted(root.rglob("*"), reverse=True):
            if d.is_dir() and d != root:
                try:
                    d.rmdir()
                except OSError as e:
                    _log.debug("删除空目录失败: %s", e)
    except Exception as _e:
        _log.debug(
            "[figma_image_store] _cleanup_empty_dirs 异常: %s", _e, exc_info=True
        )


# =============================================================================
# 节点 → 页面 解析（从 figma_mcp_cache 查找层次关系）
# =============================================================================


def _find_node_in_tree(tree: dict, target_id: str) -> dict | None:
    """在 Figma 节点树中递归查找指定 ID 的节点。"""
    if not isinstance(tree, dict):
        return None
    if tree.get("id") == target_id:
        return tree
    for child in tree.get("children", []) or []:
        found = _find_node_in_tree(child, target_id)
        if found is not None:
            return found
    return None


def _extract_pages(document: dict) -> list[dict]:
    """从 Figma document 树提取顶层页面列表（与 extract_top_pages 同逻辑）。"""
    pages: list[dict] = []
    for child in document.get("children", []) or []:
        child_type = child.get("type", "")
        child_name = child.get("name", "").strip()
        if child_type == "CANVAS":
            for frame in child.get("children", []) or []:
                if frame.get("type") in ("FRAME", "COMPONENT_SET"):
                    pages.append(
                        {
                            "id": frame.get("id", ""),
                            "name": frame.get("name", "").strip(),
                            "node": frame,
                        }
                    )
        elif child_type in ("FRAME", "COMPONENT_SET"):
            pages.append(
                {
                    "id": child.get("id", ""),
                    "name": child_name,
                    "node": child,
                }
            )
    return pages


def _resolve_page_for_node(
    file_key: str,
    node_id: str,
    project: str = "",
    conn=None,
) -> tuple[str, str]:
    """从 figma_mcp_cache 查找 node_id 所属的 Figma 页面。

    链路：figma_mcp_cache 中缓存的完整 document 树 → 提取顶层页面 →
         在每个页面子树中递归搜索 node_id → 返回 (page_id, page_name)。

    Args:
        conn: 复用已有数据库连接，为 None 则自建（自建时用完即关）。

    Returns:
        (page_id, page_name) — 找到则返回页面信息，否则返回 ("", "")。
    """
    if not file_key or not node_id:
        return "", ""

    close_conn = False
    if conn is None:
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            close_conn = True
        except ImportError:
            return "", ""

    try:
        rows = conn.execute(
            "SELECT response_json FROM figma_mcp_cache "
            "WHERE project = %s AND file_key = %s AND node_id = '' AND depth >= 1 "
            "ORDER BY depth DESC LIMIT 1",
            (project, file_key),
        ).fetchall()
    except Exception as _e:
        _log.debug("[figma_image_store] 非关键异常: %s", _e, exc_info=True)
        return "", ""
    finally:
        if close_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[figma_image_store] 非关键异常: %s", _e, exc_info=True)

    if not rows:
        return "", ""

    try:
        doc = _json.loads(rows[0]["response_json"])
    except (_json.JSONDecodeError, TypeError):
        return "", ""

    document = doc.get("document", doc)
    pages = _extract_pages(document)

    for page in pages:
        page_node = page.get("node")
        if page_node and _find_node_in_tree(page_node, node_id):
            return page["id"], page["name"]

    # 兜底：在整个 document 树中搜索（可能不在标准页面下）
    root_node = _find_node_in_tree(document, node_id)
    if root_node:
        return root_node.get("id", ""), root_node.get("name", "")

    return "", ""


# =============================================================================
# 公共 API
# =============================================================================


def import_figma_images(
    project: str = "",
    version_label: str = "",
    file_key: str = "",
    image_dir: Path | None = None,
    table_name: str = "figma_images",
    delete_after_import: bool = True,
) -> dict[str, Any]:
    """扫描 image_dir 中的图片文件，写入 MySQL 表，清理磁盘。

    Args:
        project: 项目名（默认从 config.project_name 取）
        version_label: 版本标签
        file_key: Figma 文件 KEY（默认空，由调用方按需传入）
        image_dir: 图片目录（默认 config.project_root/temp/figma_images）
        table_name: 目标表名（默认 "figma_images"）
        delete_after_import: 导入成功后是否删除磁盘文件

    Returns:
        {"success": bool, "imported": int, "errors": [str], "skipped": int}
    """
    target_dir = _resolve_image_dir(image_dir)
    project = _resolve_project(project)

    # 表名白名单校验（安全绝不偷懒）
    if not _TABLE_NAME_RE.match(table_name):
        _log.error("Figma 图片导入: 非法表名 %r", table_name)
        return {
            "success": False,
            "imported": 0,
            "errors": [f"非法表名: {table_name!r}"],
            "skipped": 0,
        }

    files = _collect_image_files(target_dir)
    if not files:
        return {"success": True, "imported": 0, "errors": [], "skipped": 0}

    imported = 0
    skipped = 0
    errors: list[str] = []

    try:
        from src.core.db_manager import get_connection
    except ImportError as e:
        _log.error("Figma 图片导入: 无法导入数据库模块: %s", e)
        return {
            "success": False,
            "imported": 0,
            "errors": [str(e)],
            "skipped": len(files),
        }

    conn = None
    try:
        conn = get_connection()
    except Exception as e:
        _log.error("Figma 图片导入: 数据库连接失败: %s", e)
        return {
            "success": False,
            "imported": 0,
            "errors": [str(e)],
            "skipped": len(files),
        }

    # 使用参数化 SQL（表名经白名单校验，字段值用占位符）
    _sql = (
        f"INSERT INTO `{table_name}` "
        "(project, version_label, file_key, page_id, page_name, node_id, file_name, "
        "image_data, image_type, width, height, image_size) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )

    # 解析 Figma file_key（若调用方未提供，尝试从 config 获取）
    if not file_key:
        try:
            from src.core.config_loader import config

            file_key = getattr(config, "figma_file_key", "") or ""
        except Exception as _e:
            _log.debug("[figma_image_store] 非关键异常: %s", _e, exc_info=True)

    try:
        for fp in files:
            try:
                data = fp.read_bytes()
                if not data:
                    skipped += 1
                    continue

                image_type = _detect_image_type(fp)
                width, height = _get_image_dimensions(data, image_type)
                node_id = _extract_node_id(fp.name)

                # ★ 解析页面归属（设计链路：node → page），复用当前连接
                page_id, page_name = _resolve_page_for_node(
                    file_key=file_key,
                    node_id=node_id,
                    project=project,
                    conn=conn,
                )

                conn.execute(
                    _sql,
                    (
                        project,
                        version_label,
                        file_key,
                        page_id,
                        page_name,
                        node_id,
                        fp.name,
                        data,
                        image_type,
                        width,
                        height,
                        len(data),
                    ),
                )
                conn.commit()

                if delete_after_import:
                    try:
                        fp.unlink()
                    except OSError as e:
                        _log.debug("删除图片文件失败: %s", e)

                imported += 1
                _log.debug(
                    "Figma 图片导入: %s → MySQL.%s (node=%s, %d bytes)",
                    fp.name,
                    table_name,
                    node_id,
                    len(data),
                )

            except Exception as e:
                _log.warning("Figma 图片导入: 单文件失败 %s: %s", fp.name, e)
                errors.append(f"{fp.name}: {e}")
                skipped += 1
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("[figma_image_store] 非关键异常: %s", _e, exc_info=True)

    if delete_after_import:
        _cleanup_empty_dirs(target_dir)

    success = len(errors) == 0
    if imported > 0:
        _log.info(
            "Figma 图片导入完成: %d 张 → MySQL.%s, %d 跳过",
            imported,
            table_name,
            skipped,
        )

    return {
        "success": success,
        "imported": imported,
        "errors": errors,
        "skipped": skipped,
    }


def list_figma_images(
    project: str,
    version_label: str = "",
    page_id: str = "",
    node_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """查询 figma_images 表，返回图片元信息列表（不含 image_data 二进制）。

    用于还原度验收、图片清单展示等消费场景。
    """
    if not project:
        return []
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        sql = (
            "SELECT id, project, version_label, file_key, page_id, page_name, "
            "node_id, file_name, image_type, width, height, image_size, created_at "
            "FROM figma_images WHERE project=%s"
        )
        params: list = [project]
        if version_label:
            sql += " AND version_label=%s"
            params.append(version_label)
        if page_id:
            sql += " AND page_id=%s"
            params.append(page_id)
        if node_id:
            sql += " AND node_id=%s"
            params.append(node_id)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in (rows or [])]
    except Exception as e:
        _log.warning("[figma_image_store] 查询失败: %s", e)
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)


def get_figma_image_data(image_id: int) -> bytes | None:
    """按 id 获取单张图片的二进制数据（image_data 字段）。"""
    if not image_id:
        return None
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT image_data, image_type FROM figma_images WHERE id=%s",
            (image_id,),
        ).fetchone()
        if row and row.get("image_data"):
            return row["image_data"]
        return None
    except Exception as e:
        _log.warning("[figma_image_store] 获取图片数据失败: %s", e)
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
