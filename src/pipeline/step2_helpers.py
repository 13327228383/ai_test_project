# -*- coding: utf-8 -*-
"""Step2 辅助函数：上传文档哈希 / 敏感数据检测 / 多源统计。

从 step2.py 拆分（§4.3 单文件≤800行红线）。
逻辑不变，仅结构重构，遵循 step2_hashes.py / step2_versioning.py 拆分模式。
"""
import json as _json
import logging
import hashlib
import re as _re_module

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 上传文档哈希辅助（增量感知）
# ═══════════════════════════════════════════════════════════════


def _compute_upload_docs_hash(project: str, version_label: str) -> str:
    """计算项目 + 版本内需求类上传文档的组合哈希（Step2 增量变更检测）。

    行集合/正文由 fetch_uploaded_documents 统一提供，与 RAG 索引、覆盖审计
    读到的是同一批文档 —— 否则"索引里有、增量检测说没变"会互相矛盾。
    """
    try:
        from src.core.upload_documents import fetch_uploaded_documents

        rows = sorted(
            fetch_uploaded_documents(project, version_label, ["requirement"]),
            key=lambda r: (str(r.get("doc_type") or ""), str(r.get("content_hash") or "")),
        )
        hashes = [
            str(r.get("content_hash", "") or "") for r in rows if r.get("content_hash")
        ]
        if not hashes:
            return ""
        return hashlib.md5("|".join(hashes).encode()).hexdigest()
    except Exception as _e:
        _log.debug("计算上传文档哈希失败: %s", _e)
        return ""


# ═══════════════════════════════════════════════════════════════
# ★ S2-018: 敏感数据检测（身份证 / 手机号 / 邮箱 / IP）
# ═══════════════════════════════════════════════════════════════

# 默认敏感数据正则规则集（§4.10）
_SENSITIVE_PATTERNS = [
    _re_module.compile(r"\b1[3-9]\d{9}\b"),  # 手机号（11位）
    _re_module.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),  # 身份证号（18位）
    _re_module.compile(
        r"\b[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}\b"
    ),  # 身份证号（15位，2位年份）
    _re_module.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # 邮箱
    _re_module.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
    ),  # IPv4
]


def _detect_sensitive_data(text: str) -> bool:
    """★ S2-018: 检测文本是否包含敏感数据。

    默认规则集：
    - 身份证号（18位/15位）
    - 手机号（11位）
    - 邮箱地址
    - IPv4 地址

    返回 True 表示检测到敏感数据。
    """
    if not text or not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


def _mark_sensitive_data(reqs: list) -> int:
    """★ S2-018: 对需求列表标记 contains_sensitive_data 字段。

    扫描每条需求的 operation / expected_feedback / description / source_quote 字段，
    任一字段命中敏感模式则标记为 True。

    返回命中敏感数据的需求条目数。
    """
    if not reqs:
        return 0
    hit_count = 0
    for req in reqs:
        if not isinstance(req, dict):
            continue
        # 拼接所有可能含敏感信息的字段
        combined = " ".join(
            [
                str(req.get("operation", "") or ""),
                str(req.get("expected_feedback", "") or ""),
                str(req.get("description", "") or ""),
                str(req.get("source_quote", "") or ""),
            ]
        )
        is_sensitive = _detect_sensitive_data(combined)
        req["contains_sensitive_data"] = is_sensitive
        if is_sensitive:
            hit_count += 1
    return hit_count


# ═══════════════════════════════════════════════════════════════
# 多源功能提取统计（KPI 三方对齐）
# ═══════════════════════════════════════════════════════════════


def _query_multi_source_stats(project_name: str, version_label: str) -> dict[str, int]:
    """★ #14 KPI 三方对齐：从 system_feature_list 表查询多源功能提取统计。

    按 source 字段分组统计各来源的功能页面数和功能项总数。

    Args:
        project_name: 项目名
        version_label: 版本标签

    Returns:
        {
            "dom_pages": int, "dom_features": int,
            "figma_pages": int, "figma_features": int,
            "requirement_pages": int, "requirement_features": int,
            "yapi_pages": int, "yapi_features": int,
            "total_pages": int, "total_features": int,
        }
    """
    stats = {
        "dom_pages": 0,
        "dom_features": 0,
        "figma_pages": 0,
        "figma_features": 0,
        "requirement_pages": 0,
        "requirement_features": 0,
        "yapi_pages": 0,
        "yapi_features": 0,
        "total_pages": 0,
        "total_features": 0,
    }
    if not project_name or not version_label:
        return stats

    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            return stats
        rows = (
            conn.execute(
                "SELECT source, features_json FROM system_feature_list "
                "WHERE project = %s AND version_label = %s",
                (project_name, version_label),
            ).fetchall()
            or []
        )

        for row in rows:
            source = (row.get("source", "dom") or "dom").lower()
            try:
                features = _json.loads(row.get("features_json", "[]") or "[]")
                if not isinstance(features, list):
                    features = []
            except (_json.JSONDecodeError, TypeError):
                features = []

            # 映射 source 到 stats key
            if source == "dom":
                stats["dom_pages"] += 1
                stats["dom_features"] += len(features)
            elif source == "figma":
                stats["figma_pages"] += 1
                stats["figma_features"] += len(features)
            elif source == "requirement":
                stats["requirement_pages"] += 1
                stats["requirement_features"] += len(features)
            elif source == "yapi":
                stats["yapi_pages"] += 1
                stats["yapi_features"] += len(features)

        stats["total_pages"] = (
            stats["dom_pages"]
            + stats["figma_pages"]
            + stats["requirement_pages"]
            + stats["yapi_pages"]
        )
        stats["total_features"] = (
            stats["dom_features"]
            + stats["figma_features"]
            + stats["requirement_features"]
            + stats["yapi_features"]
        )
    except Exception as e:
        _log.debug("[Step2] _query_multi_source_stats 异常(不阻塞): %s", str(e)[:80])
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
    return stats


# ═══════════════════════════════════════════════════════════════
# P2-11 RAG 需求索引（防幻觉）
# ═══════════════════════════════════════════════════════════════


def index_requirements_to_rag(project: str, all_reqs: list) -> int:
    """★ P2-11: 需求拆解后索引到 multimodal_chunks（RAG 防幻觉）。

    从 step2.py 拆分（§4.3 单文件≤800行）。
    根因: index_requirements 原在 step2.py 内联 → step2.py 超限
    通用性: content=operation+object+expected，不含业务术语；与 dom_store.py 模式一致
    降级: 索引失败非阻断，返回 0，Step3 降级无 requirement RAG 上下文
    职责边界: Step2 只写索引，检索由 Step3 _contexts 负责
    """
    try:
        from src.rag.integration import index_requirements

        _rag_count = index_requirements(project or "", "", all_reqs)
        if _rag_count > 0:
            _log.info("[Step2] ✓ RAG 需求索引: %d chunks", _rag_count)
        return int(_rag_count or 0)
    except Exception as _rag_e:
        _log.warning("[Step2] RAG 需求索引失败(不阻断): %s", str(_rag_e)[:120])
        return 0
