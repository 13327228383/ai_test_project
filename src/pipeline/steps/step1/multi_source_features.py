# -*- coding: utf-8 -*-
from __future__ import annotations

"""
多源功能清单提取器
==================
从 Figma 设计稿提取候选功能，写入 system_feature_list 表（source=figma）。

数据流：
    Figma → figma_nodes / figma_pages 表 → 功能清单（source=figma, status=designed）
        ↓
    system_feature_list 表
        ↓
    Step2 feature_driven._load_features_from_db 读取

需求文档与 YApi 不作为功能清单来源：需求是 Step2 的拆解对象（再当功能源会自匹配
循环），YApi 已在 Step1 RAG 构建时切片为 rag_chunks(type=api) 供 Step3 检索。
"""

import json
import logging

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
# 统一写入入口
# ══════════════════════════════════════════════════


def _save_features_to_db(
    project: str,
    version_label: str,
    source: str,
    source_status: str,
    page_url: str,
    page_path: str,
    page_title: str,
    features: list[dict],
    statistics: dict | None = None,
) -> int:
    """写入功能清单到 system_feature_list 表（按 source 删除后插入）。

    Args:
        project: 项目名
        version_label: 版本标签
        source: 功能来源（figma/requirement/yapi）
        source_status: 功能状态（designed/planned/api_only）
        page_url/page_path/page_title: 页面信息
        features: 功能列表 [{"type": str, "name": str, "description": str}, ...]
        statistics: 统计信息

    Returns:
        写入的功能条数
    """
    if not features:
        return 0

    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            return 0
        try:
            cursor = conn.cursor()
            # 按 source 删除旧的（同源覆盖，不污染其他来源）
            cursor.execute(
                "DELETE FROM system_feature_list "
                "WHERE project = %s AND version_label = %s AND source = %s",
                (project, version_label, source),
            )
            count = 0
            for feat in features:
                cursor.execute(
                    """INSERT INTO system_feature_list
                       (project, version_label, page_url, page_path, page_title,
                        features_json, statistics_json, source, source_status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (
                        project,
                        version_label,
                        page_url,
                        page_path,
                        page_title,
                        json.dumps(
                            (
                                feat.get("features", [feat])
                                if "features" in feat
                                else [feat]
                            ),
                            ensure_ascii=False,
                        ),
                        json.dumps(statistics or {}, ensure_ascii=False),
                        source,
                        source_status,
                    ),
                )
                count += 1
            conn.commit()
            _log.info(
                "[multi_source_features] ✓ 写入 %s 来源功能清单: %d 条 (project=%s)",
                source,
                count,
                project,
            )
            return count
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
    except Exception as e:
        _log.warning(
            "[multi_source_features] 写入 %s 功能清单失败: %s", source, str(e)[:120]
        )
        return 0


# ══════════════════════════════════════════════════
# 1. Figma → 功能清单
# ══════════════════════════════════════════════════


def extract_features_from_figma(project: str, version_label: str) -> int:
    """从 figma_nodes / figma_pages 表提取功能清单，写入 system_feature_list。

    Figma 节点表示"设计稿中规划的功能"，对应 source_status=designed。
    当 figma_nodes 为空时，回退到 figma_pages（页面级功能标注）。

    Returns:
        写入的功能条数
    """
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            return 0

        # 先尝试 figma_nodes（节点级，粒度更细）
        try:
            rows = conn.execute(
                "SELECT n.node_id, n.name, n.node_type, n.text_content, "
                "COALESCE(p.name, '') as page_name, COALESCE(p.canvas, '') as canvas "
                "FROM figma_nodes n LEFT JOIN figma_pages p "
                "ON n.page_id = p.page_id AND n.project = p.project AND n.version_label = p.version_label "
                "WHERE n.project = %s AND n.version_label = %s "
                "AND (n.text_content IS NOT NULL OR (n.name IS NOT NULL AND n.name != '')) "
                "ORDER BY p.canvas, p.name, n.name LIMIT 500",
                (project, version_label),
            ).fetchall()
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)

        if rows:
            # 节点级数据可用，按原逻辑处理
            return _extract_from_figma_nodes(project, version_label, rows)

        # ★ fallback: figma_nodes 为空，使用 figma_pages（页面级）
        _log.info(
            "[multi_source_features] figma_nodes 为空，回退到 figma_pages 页面级提取"
        )
        return _extract_from_figma_pages(project, version_label)

    except Exception as e:
        _log.warning("[multi_source_features] Figma 功能提取失败: %s", str(e)[:120])
        return 0


def _extract_from_figma_nodes(project: str, version_label: str, rows) -> int:
    """从 figma_nodes 提取功能清单（节点级）。"""
    # 按画布/页面分组
    page_groups: dict[str, dict] = {}
    for r in rows:
        canvas = r.get("canvas", "") or "未分类"
        page_name = r.get("page_name", "") or "未分类"
        group_key = f"{canvas}/{page_name}"
        if group_key not in page_groups:
            page_groups[group_key] = {
                "page_url": f"figma://{canvas}/{page_name}",
                "page_path": f"/figma/{canvas}",
                "page_title": page_name,
                "features": [],
            }
        node_name = r.get("name", "") or ""
        node_type = r.get("node_type", "") or ""
        text = r.get("text_content", "") or ""
        # 把 Figma 节点映射为功能项
        feat_type = _infer_figma_node_type(node_type, node_name, text)
        page_groups[group_key]["features"].append(
            {
                "type": feat_type,
                "name": node_name,
                "description": f"Figma设计-{node_type}: {node_name}"
                + (f" ({text[:50]})" if text else ""),
            }
        )

    # 按页面写入
    total = 0
    for group_key, group in page_groups.items():
        stats = {"total": len(group["features"])}
        total += _save_features_to_db(
            project,
            version_label,
            source="figma",
            source_status="designed",
            page_url=group["page_url"],
            page_path=group["page_path"],
            page_title=group["page_title"],
            features=group["features"],
            statistics=stats,
        )
    return total


def _extract_from_figma_pages(project: str, version_label: str) -> int:
    """从 figma_pages 提取功能清单（页面级，当 figma_nodes 不可用时）。"""
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            return 0
        try:
            rows = conn.execute(
                "SELECT name, canvas FROM figma_pages "
                "WHERE project = %s AND version_label = %s "
                "ORDER BY canvas, name",
                (project, version_label),
            ).fetchall()
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)

        if not rows:
            _log.debug("[multi_source_features] Figma pages 无数据")
            return 0

        # ★ 批量写入：先收集所有页面特征，再一次性写入（避免逐条 DELETE+INSERT 覆盖）
        all_features: list[dict] = []
        for r in rows:
            page_name = (r.get("name", "") or "").strip()
            canvas = (r.get("canvas", "") or "").strip()
            if not page_name or len(page_name) < 2:
                continue
            feat_type = _infer_figma_node_type("PAGE", page_name, "")
            all_features.append(
                {
                    "page_url": (
                        f"figma://{canvas}/{page_name}"
                        if canvas
                        else f"figma://{page_name}"
                    ),
                    "page_path": f"/figma/{canvas}" if canvas else "/figma",
                    "page_title": page_name,
                    "features": [
                        {
                            "type": feat_type,
                            "name": page_name,
                            "description": f"Figma页面: {page_name}"
                            + (f" (画布: {canvas})" if canvas else ""),
                        }
                    ],
                    "statistics": {"total": 1},
                }
            )

        # 批量写入：先清除旧数据，再插入所有
        conn2 = get_connection()
        if not conn2:
            return 0
        try:
            cursor = conn2.cursor()
            cursor.execute(
                "DELETE FROM system_feature_list "
                "WHERE project = %s AND version_label = %s AND source = 'figma'",
                (project, version_label),
            )
            import json as _json2

            count = 0
            for feat in all_features:
                cursor.execute(
                    """INSERT INTO system_feature_list
                       (project, version_label, page_url, page_path, page_title,
                        features_json, statistics_json, source, source_status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'figma', 'designed', NOW())""",
                    (
                        project,
                        version_label,
                        feat["page_url"],
                        feat["page_path"],
                        feat["page_title"],
                        _json2.dumps(feat["features"], ensure_ascii=False),
                        _json2.dumps(feat["statistics"], ensure_ascii=False),
                    ),
                )
                count += 1
            conn2.commit()
            _log.info(
                "[multi_source_features] ✓ 批量写入 figma 来源功能清单: %d 条 (project=%s)",
                count,
                project,
            )
            return count
        finally:
            try:
                conn2.close()
            except Exception as _e:
                _log.debug("conn2.close 失败: %s", _e, exc_info=True)
    except Exception as e:
        _log.warning("[multi_source_features] Figma pages 提取失败: %s", str(e)[:120])
        return 0


def _infer_figma_node_type(node_type: str, name: str, text: str) -> str:
    """根据 Figma 节点信息推断 DOM 元素类型（与 dom_action_map 对齐）。"""
    combined = f"{node_type} {name} {text}".lower()
    if any(k in combined for k in ["button", "btn", "按钮"]):
        return "button"
    if any(k in combined for k in ["input", "textarea", "textbox", "输入"]):
        return "input"
    if any(k in combined for k in ["link", "href", "链接"]):
        return "link"
    if any(k in combined for k in ["table", "grid", "表格"]):
        return "table"
    if any(k in combined for k in ["chart", "graph", "图表"]):
        return "chart"
    if any(k in combined for k in ["select", "dropdown", "下拉"]):
        return "dropdown"
    if any(k in combined for k in ["tab", "标签页"]):
        return "tab"
    if any(k in combined for k in ["modal", "dialog", "弹窗"]):
        return "modal"
    if any(k in combined for k in ["upload", "上传"]):
        return "upload"
    if any(k in combined for k in ["search", "搜索"]):
        return "search"
    if any(k in combined for k in ["switch", "toggle", "开关"]):
        return "switch"
    if any(k in combined for k in ["tree", "树"]):
        return "tree"
    if any(k in combined for k in ["calendar", "date", "日期"]):
        return "calendar"
    if any(k in combined for k in ["slider", "滑块"]):
        return "slider"
    if any(k in combined for k in ["cascader", "级联"]):
        return "cascader"
    if any(k in combined for k in ["steps", "步骤"]):
        return "steps"
    if any(k in combined for k in ["timeline", "时间线"]):
        return "timeline"
    return "other"


# ══════════════════════════════════════════════════
# 需求/YApi 不作为功能清单来源，原因见模块 docstring
# ══════════════════════════════════════════════════


# ══════════════════════════════════════════════════
# 4. 统一入口：Step1 完成后调用
# ══════════════════════════════════════════════════


def run_multi_source_feature_extraction(
    project: str, version_label: str
) -> dict[str, int]:
    """统一入口：从 Figma 设计稿提取功能清单（Step1 完成后调用）。

    Args:
        project: 项目名
        version_label: 版本标签

    Returns:
        {"figma": 写入条数}；无项目/版本上下文时返回 0。
    """
    if not project or not version_label:
        return {"figma": 0}

    _log.info(
        "[multi_source_features] 开始功能提取 (project=%s, version=%s)",
        project,
        version_label,
    )

    figma_count = extract_features_from_figma(project, version_label)

    _log.info("[multi_source_features] ✓ 功能提取完成: figma=%d", figma_count)

    return {"figma": figma_count}
