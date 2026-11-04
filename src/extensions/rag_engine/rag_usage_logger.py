# -*- coding: utf-8 -*-
"""★ RAG 使用日志共享工具 — 供 Step2/Step3/Step4/校验器/分析器等所有 RAG 检索点共用。

所有调用 rag_engine.retrieve() 的地方，检索完成后调用本模块的 log_rag_retrieval()，
即可自动将 RAG 来源记录写入 rag_usage_log 表。
"""

import logging
from collections import defaultdict

_log = logging.getLogger(__name__)

# entity_refs type → rag_usage_log.rag_source_type 映射
_RAG_TYPE_MAP = {
    "api": "api_doc",
    "db_table": "db_schema",
    "figma_node": "figma_page",
    "dom": "figma_page",  # DOM 快照归入 figma_page 大类
}


def _extract_entity_refs_from_retrieved(retrieved: dict) -> list[dict]:
    """从 rag_engine.retrieve() 返回的 dict 中提取 entity_refs 列表。

    与 retrieve_and_resolve 中的 entity_refs 构建逻辑一致。
    """
    entity_refs = []
    for _section_name, chunks in retrieved.items():
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            refs = chunk.get("entity_refs")
            if not refs:
                chunk_type = chunk.get("type", "")
                chunk_id = chunk.get("chunk_id", "")
                if chunk_type == "api":
                    refs = [{"type": "api", "chunk_id": chunk_id}]
                elif chunk_type == "db_table":
                    refs = [
                        {
                            "type": "db_table",
                            "id": chunk_id.replace("db:", "", 1),
                            "chunk_id": chunk_id,
                        }
                    ]
                elif chunk_type == "figma_node":
                    refs = [
                        {
                            "type": "figma_node",
                            "id": chunk_id.replace("figma_node:", "", 1),
                            "chunk_id": chunk_id,
                        }
                    ]
                elif chunk_type == "dom":
                    refs = [{"type": "dom", "chunk_id": chunk_id}]
            if refs:
                entity_refs.extend(refs)
    return entity_refs


def _entity_refs_to_records(entity_refs: list[dict], req_id: str = "") -> list[dict]:
    """将 entity_refs 转换为 rag_usage_log 写入记录格式。

    返回: [{"rag_source_type": str, "rag_source_id": str, "field_used": str, "req_id": str}, ...]
    """
    records = []
    seen = set()
    for ref in entity_refs or []:
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type", "")
        source_type = _RAG_TYPE_MAP.get(ref_type)
        if not source_type:
            continue
        source_id = str(ref.get("id") or ref.get("chunk_id") or "")
        if not source_id:
            continue
        dedup_key = (source_type, source_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        field_used = source_id
        if ref_type == "db_table" and source_id.startswith("db:"):
            field_used = source_id[3:]
        elif ref_type == "figma_node" and source_id.startswith("figma_node:"):
            field_used = source_id[12:]

        records.append(
            {
                "rag_source_type": source_type,
                "rag_source_id": source_id[:256],
                "field_used": field_used[:256],
                "req_id": str(req_id or ""),
            }
        )
    return records


def log_rag_retrieval(
    retrieved: dict,
    *,
    project: str = "",
    version_id: int = 0,
    case_id: str = "",
    check_id: str = "",
    req_id: str = ""
) -> int:
    """从 retrieve() 返回结果中提取 entity_refs 并批量写入 rag_usage_log。

    适用于所有调用 rag_engine.retrieve() 的地方。自动提取 entity_refs、去重、写入。

    Args:
        retrieved: rag_engine.retrieve() 的返回 dict
        project: 项目名
        version_id: 版本 ID
        case_id: 用例 ID（可为空）
        check_id: 检查点 ID（可为空，默认 fallback 为 case_id）
        req_id: 需求 ID

    Returns:
        写入的记录数（0 = 无记录或写入失败）
    """
    if not retrieved or not isinstance(retrieved, dict):
        return 0

    entity_refs = _extract_entity_refs_from_retrieved(retrieved)
    if not entity_refs:
        return 0

    records = _entity_refs_to_records(entity_refs, req_id=req_id)
    if not records:
        return 0

    if not project:
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug(
                "[rag_usage_logger] 加载 project 配置异常: %s", _e, exc_info=True
            )

    if not check_id:
        check_id = case_id or ""

    conn = None
    written = 0
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        rows = [
            (
                project,
                version_id,
                case_id or "",
                check_id,
                rec["req_id"],
                rec["rag_source_type"],
                rec["rag_source_id"],
                rec["field_used"],
            )
            for rec in records
        ]
        conn.executemany(
            "INSERT INTO rag_usage_log(project, version_id, case_id, check_id, "
            "req_id, rag_source_type, rag_source_id, field_used) "
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        written = len(rows)
        _log.debug(
            "[RAG-Usage] 写入 %d 条记录 (project=%s case=%s)", written, project, case_id
        )
    except Exception as e:
        _log.debug("[RAG-Usage] 写入失败(不阻塞): %s", str(e)[:80])
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.debug("[rag_usage_logger] 回滚异常: %s", _e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[rag_usage_logger] 关闭连接异常: %s", _e, exc_info=True)
    return written
