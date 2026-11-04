# -*- coding: utf-8 -*-
"""建模引擎 - RAG 模块（从 _modeling_engine.py 拆分，单文件<=800行）。

职责：RAG 增强 + RAG 使用记录写入/查询。
  _augment_with_multimodal_rag: 多模态 RAG 增强（需求+DOM+figma 联合检索）
  _extract_rag_usage_records: 从 entity_refs 提取 RAG 使用记录
  _write_rag_usage_logs: 批量写入 RAG 使用日志
  list_rag_usage_log: 查询 RAG 使用日志（外部 API 调用）

被 _modeling_engine.py 主流程调用（_enrich_requirements + _parallel_model_all_reqs）。
依赖：_log (logging.getLogger)

根因(3层追问):
  1. 表层: _modeling_engine.py 超 §4.3 红线
  2. 中层: RAG 函数群与主流程内聚，但逻辑独立
  3. 根因: RAG 是独立的"检索增强"职责，应拆为独立模块
通用性: 适用于所有系统/行业 - 纯 RAG 检索/记录，不依赖业务术语
"""
import logging

_log = logging.getLogger(__name__)

# ★ P2-11 MultiModalRAG 每来源类型检索条数（跨模态覆盖，防止单模态偏置）
#   原定义在 _modeling_engine.py:94，拆分到本模块后漏 import → NameError 被除兜住返回空串
#   → 防幻觉注入沉默失效（违反§执行纪律7"沉默即失败"）
#   修复(2026-07-25): 常量就近定义在唯一消费方，避免循环 import
_MM_RAG_TOP_K_PER_SOURCE = 2
# ★ P2-11 MultiModalRAG 注入文本最大字符数（防 prompt 膨胀）
_MM_RAG_MAX_CHARS = 1200

# ★ D.1g 拆分遗留断链修复(2026-07-25): _RAG_TYPE_MAP 原定义在 _modeling_engine.py:303
#   拆分到本模块后漏 import → NameError 被 except 兜住 → rag_usage_log 表无数据
#   修复: 就近定义在唯一消费方（主文件自身不使用），避免循环 import
# entity_refs type → rag_usage_log.rag_source_type 映射
_RAG_TYPE_MAP = {
    "api": "api_doc",
    "db_table": "db_schema",
    "figma_node": "figma_page",
}


def _augment_with_multimodal_rag(req: dict, query: str) -> str:
    """★ P2-11: 用 MultiModalRAG 跨模态检索增强 RAG 上下文（防幻觉）。

    从 multimodal_chunks 表检索 requirement+dom+figma 跨模态证据，
    格式化为简洁文本追加到 _rag，为 AI 补漏路径提供 DOM 真实控件约束。

    通用性（§12.4）:
      1. 跨系统: multimodal_chunks 是通用结构，无业务术语
      2. 跨行业: 任何需求/DOM/设计稿文本都适用
      3. 降级: 无数据/异常 → 返回空串，不阻断主流程
      4. 职责边界: 仅增强上下文，不替代 rag_engine 主链路

    返回: 增强文本（空串表示无增强）
    """
    if not query:
        return ""
    try:
        from src.rag.retriever import get_retriever
        from src.core.config_loader import config as _cfg

        _project = getattr(_cfg, "project_name", "") or ""
        if not _project:
            return ""
        _retriever = get_retriever()
        _results = _retriever.retrieve_cross_modal(
            _project,
            query,
            top_k_per_source=_MM_RAG_TOP_K_PER_SOURCE,
        )
        if not _results:
            return ""
        _parts = ["\n## 跨模态事实源（MultiModalRAG · 防幻觉约束）"]
        _total_chars = 0
        for _st, _chunks in _results.items():
            if not _chunks:
                continue
            _parts.append("### %s 证据:" % _st)
            for _c in _chunks:
                _content = (_c.get("content") or "").strip()
                if not _content:
                    continue
                # 截断单条过长内容
                if len(_content) > 200:
                    _content = _content[:200] + "..."
                _line = "- [score=%s] %s" % (
                    _c.get("score", 0),
                    _content.replace("\n", " "),
                )
                if _total_chars + len(_line) > _MM_RAG_MAX_CHARS:
                    break
                _parts.append(_line)
                _total_chars += len(_line)
        if len(_parts) <= 1:
            return ""
        _parts.append(
            "★ 以上为真实需求/DOM/设计稿证据，生成 actions/checks 必须基于此事实源，禁止编造不存在的控件或路径。"
        )
        return "\n".join(_parts)
    except Exception as _e:
        _log.debug("[Step3] _augment_with_multimodal_rag 异常: %s", str(_e)[:80])
        return ""


def _extract_rag_usage_records(req_id: str, entity_refs: list[dict]) -> list[dict]:
    """★ S3-012: 从 entity_refs 提取 RAG 使用记录列表。

    将 RAG 检索返回的 entity_refs 转换为 rag_usage_log 记录格式：
    - rag_source_type: db_schema / api_doc / figma_page
    - rag_source_id:   表名 / API chunk_id / 页面 ID
    - field_used:      表名 / API 路径 / 页面名称（从 entity_ref 中提取）
    """
    records = []
    seen = set()  # 去重：(source_type, source_id)
    for ref in entity_refs or []:
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type", "")
        source_type = _RAG_TYPE_MAP.get(ref_type)
        if not source_type:
            continue
        # 提取 source_id：优先 id，其次 chunk_id
        source_id = str(ref.get("id") or ref.get("chunk_id") or "")
        if not source_id:
            continue
        # 去重
        dedup_key = (source_type, source_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        # field_used: 从 source_id 提取可读字段名
        field_used = source_id
        if ref_type == "db_table" and source_id.startswith("db:"):
            field_used = source_id[3:]
        elif ref_type == "figma_node" and source_id.startswith("figma_node:"):
            field_used = source_id[12:]
        records.append(
            {
                "req_id": req_id,
                "rag_source_type": source_type,
                "rag_source_id": source_id[:256],
                "field_used": field_used[:256],
            }
        )
    return records


def _write_rag_usage_logs(
    flat_intents: list[dict], rag_usage_map: dict[str, list[dict]]
) -> int:
    """★ S3-012: 将 RAG 使用日志批量写入 MySQL rag_usage_log 表。

    在 _write_intents_to_store 用例写入成功后调用。
    通过 req_id 关联 RAG 使用记录和用例的 case_id/check_id。

    返回写入的记录数。
    """
    if not flat_intents or not rag_usage_map:
        return 0
    try:
        from src.core.config_loader import config
        from src.core.versioning import current_version_id
        from src.core.db_manager import get_connection

        project = getattr(config, "project_name", "") or ""
        vid = current_version_id(project)
    except Exception as e:
        _log.debug("[S3-012] 读取 project/version_id 失败: %s", e)
        return 0

    # 收集所有待写入记录
    rows = []
    for intent in flat_intents:
        if not isinstance(intent, dict):
            continue
        req_id = intent.get("req_id", "")
        case_id = intent.get("case_id", "")
        if not req_id or not case_id:
            continue
        usage_records = rag_usage_map.get(req_id)
        if not usage_records:
            continue
        # 为每个 check 写入 RAG 使用记录
        checks = intent.get("checks") or []
        if not checks:
            # 无 checks 时，用 case_id 作为 check_id 写入一条
            for rec in usage_records:
                rows.append(
                    (
                        project,
                        vid,
                        case_id,
                        case_id,
                        req_id,
                        rec["rag_source_type"],
                        rec["rag_source_id"],
                        rec["field_used"],
                    )
                )
        else:
            for check in checks:
                if not isinstance(check, dict):
                    continue
                check_id = str(check.get("check_id") or check.get("id") or "")
                if not check_id:
                    check_id = case_id
                for rec in usage_records:
                    rows.append(
                        (
                            project,
                            vid,
                            case_id,
                            check_id,
                            req_id,
                            rec["rag_source_type"],
                            rec["rag_source_id"],
                            rec["field_used"],
                        )
                    )

    if not rows:
        return 0

    conn = None
    written = 0
    try:
        conn = get_connection()
        # 批量 INSERT（单次往返）
        conn.executemany(
            "INSERT INTO rag_usage_log(project, version_id, case_id, check_id, "
            "req_id, rag_source_type, rag_source_id, field_used) "
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        written = len(rows)
        _log.info("[S3-012] rag_usage_log 写入 %d 条记录", written)
    except Exception as e:
        _log.warning("[S3-012] rag_usage_log 写入失败: %s", str(e)[:120])
        if conn:
            try:
                conn.rollback()
            except Exception as _re:
                _log.debug("[S3-012] rollback 失败: %s", _re, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _ce:
                _log.debug("[S3-012] conn.close 失败: %s", _ce, exc_info=True)
    return written


def count_rag_usage_log(
    *,
    project: str = "",
    version_id: int = 0,
    case_id: str = "",
    rag_source_type: str = "",
):
    """★ RAG 使用日志总条数（与 list_rag_usage_log 相同过滤口径）。"""
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        where = []
        params = []
        if project:
            where.append("project=%s")
            params.append(project)
        if version_id:
            where.append("version_id=%s")
            params.append(version_id)
        if case_id:
            where.append("case_id=%s")
            params.append(case_id)
        if rag_source_type:
            where.append("rag_source_type=%s")
            params.append(rag_source_type)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM rag_usage_log" + where_clause,
            params,
        ).fetchone()
        return row["total"] if isinstance(row, dict) else (row[0] if row else 0)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug(
                    "[count_rag_usage_log] conn.close 失败: %s", _e, exc_info=True
                )


def list_rag_usage_log(
    *,
    project: str = "",
    version_id: int = 0,
    case_id: str = "",
    rag_source_type: str = "",
    limit: int = 100,
    offset: int = 0
):
    """查询 RAG 使用日志列表，支持按 project/version/case/source_type 过滤。返回 dict 列表。"""
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        where = []
        params = []
        if project:
            where.append("project=%s")
            params.append(project)
        if version_id:
            where.append("version_id=%s")
            params.append(version_id)
        if case_id:
            where.append("case_id=%s")
            params.append(case_id)
        if rag_source_type:
            where.append("rag_source_type=%s")
            params.append(rag_source_type)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.extend([limit, offset])
        rows = conn.execute(
            "SELECT id, project, version_id, case_id, check_id, req_id, "
            "rag_source_type, rag_source_id, field_used, created_at "
            "FROM rag_usage_log"
            + where_clause
            + " ORDER BY id DESC LIMIT %s OFFSET %s",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = (
                dict(r)
                if isinstance(r, dict)
                else {
                    "id": r[0],
                    "project": r[1],
                    "version_id": r[2],
                    "case_id": r[3],
                    "check_id": r[4],
                    "req_id": r[5],
                    "rag_source_type": r[6],
                    "rag_source_id": r[7],
                    "field_used": r[8],
                    "created_at": r[9],
                }
            )
            result.append(d)
        return result
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug(
                    "[list_rag_usage_log] conn.close 失败: %s", _e, exc_info=True
                )
