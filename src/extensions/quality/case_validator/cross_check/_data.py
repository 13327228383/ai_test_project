# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：交叉校验数据加载
"""

import json
from src.core.guarded import guard
import logging
import threading

_log = logging.getLogger(__name__)

# ── P0 防幻觉：交叉校验数据缓存（项目级，惰性加载）──
_cross_check_cache = {}  # {project_root: {yapi_paths, figma_elements, db_enums}}
_cross_check_lock = threading.Lock()


def load_cross_check_data() -> dict:
    """
    ★ MySQL-only：惰性加载项目级交叉校验数据源。

    唯一结构化事实源：
        - MySQL ``diff_snapshots`` 表（api_schema / db_schema 类型）
        - MySQL ``figma_pages`` + ``figma_nodes`` 表（★ 2026-06-30: 替代 system_nav_maps）

    磁盘 JSON（``raw_yapi_docs.json`` /
    ``system_data_catalog.json``）已降级为"显式导入源 / 附件"，
    不参与主运行链路兜底；若确需从磁盘恢复，请先通过
    ``diff_snapshot_store.import_from_json(...)`` 写入 MySQL。

    返回: {"yapi_paths": set, "figma_elements": set, "db_enums": dict}
    """
    try:
        from src.core.config_loader import config

        root = str(config.project_root)
    except Exception:
        root = None

    if root is None:
        return {"yapi_paths": set(), "figma_elements": set(), "db_enums": {}}

    with _cross_check_lock:
        if root in _cross_check_cache:
            return _cross_check_cache[root]

    data = {
        "yapi_paths": set(),
        "figma_elements": set(),
        "db_enums": {},
        "db_tables": set(),
        "db_columns": {},
    }

    try:
        # 1. 加载 YApi 接口路径（MySQL api_schema 快照）
        try:
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            yapi_raw = diff_snapshot_store.load_snapshot("api_schema")
            if yapi_raw:
                # 兼容多层封装：yapi_client 返回 {data:{...}} / {...}
                items = (
                    yapi_raw.get("data", yapi_raw)
                    if isinstance(yapi_raw, dict)
                    else yapi_raw
                )
                if isinstance(items, dict):
                    items = items.get(
                        "list", items.get("apis", items.get("interfaces", []))
                    )
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            method = str(item.get("method", "")).upper()
                            path = item.get("path", "").strip()
                            if method and path:
                                data["yapi_paths"].add(f"{method} {path}")
                                data["yapi_paths"].add(path)  # 纯路径也存
        except Exception as _e:
            _log.debug("yapi + yapi: %s", _e, exc_info=True)

        # 2. 加载 Figma 页面元素（★ 2026-06-30: 从 figma_pages + figma_nodes 表读取，替代 nav_map）
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
            version_label = str(getattr(config, "project_version", "") or "")
            if project and version_label:
                from src.core.db_manager import get_connection

                conn = get_connection()
                try:
                    # 页面名
                    rows = conn.execute(
                        "SELECT name FROM figma_pages WHERE project=%s AND version_label=%s",
                        (project, version_label),
                    ).fetchall()
                    for r in rows or []:
                        if r["name"]:
                            data["figma_elements"].add(r["name"])
                    # 节点名 + 文本内容
                    node_rows = conn.execute(
                        "SELECT name, text_content FROM figma_nodes "
                        "WHERE project=%s AND version_label=%s AND (text_content IS NOT NULL OR name IS NOT NULL) LIMIT 500",
                        (project, version_label),
                    ).fetchall()
                    for n in node_rows or []:
                        if n.get("name"):
                            data["figma_elements"].add(n["name"])
                        tc = (n.get("text_content") or "").strip()
                        if tc and len(tc) < 200:
                            data["figma_elements"].add(tc)
                finally:
                    with guard("load_cross_check_data: conn.close()", log=_log):
                        conn.close()
        except Exception as _e:
            _log.debug("fetchall + figma + figma: %s", _e, exc_info=True)

        # 3. 加载 DB 字段枚举（MySQL db_schema 快照）
        try:
            from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

            db_snap = diff_snapshot_store.load_snapshot("db_schema")
            # 格式一：{classification: {table: {columns: {col: {enum_values/sample_values:[...]}}}}}
            # 格式二：{schema_text: "..."}（Step2 持久化时的朴素格式）
            catalog = None
            if isinstance(db_snap, dict):
                if "classification" in db_snap:
                    catalog = db_snap
                elif "schema_text" in db_snap:
                    # schema_text 不保证可解析成枚举；当前版本仅以 classification 形式提供
                    pass
            if catalog:
                classification = catalog.get("classification", {})
                for table_name, tinfo in classification.items():
                    if isinstance(tinfo, dict):
                        data["db_tables"].add(str(table_name).lower())
                        cols = tinfo.get("columns", {})
                        col_set = set()
                        if isinstance(cols, dict):
                            for col_name, col_info in cols.items():
                                col_set.add(str(col_name).lower())
                                if isinstance(col_info, dict):
                                    enum_vals = col_info.get(
                                        "enum_values", col_info.get("sample_values", [])
                                    )
                                    if enum_vals:
                                        data["db_enums"][f"{table_name}.{col_name}"] = (
                                            set(str(v).lower() for v in enum_vals)
                                        )
                        data["db_columns"][str(table_name).lower()] = col_set
        except Exception as _e:
            _log.debug("未知: %s", _e, exc_info=True)

    except Exception as _e:
        _log.debug("未知: %s", _e, exc_info=True)

    with _cross_check_lock:
        _cross_check_cache[root] = data
    return data
