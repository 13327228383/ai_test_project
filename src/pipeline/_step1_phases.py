# -*- coding: utf-8 -*-
"""Step1 Phase 函数模块 — 主函数只保留调度，各 Phase 实现放这里。

  - _run_prototype_recognition_phase: Phase 3.1 原型图视觉识别
  - check_step1_blocking_failures: Phase 3.6 数据源失败判定
  - _build_rag_index_phase: Phase 3.7 RAG 增量构建
"""
import logging
from src.core.guarded import guard

from src.core.config_loader import config

_log = logging.getLogger(__name__)


# ── Phase 3.1: 原型图视觉识别 ──────────────────────────────────────────
def _run_prototype_recognition_phase(image_urls, sources_status):
    """Phase 3.1: 从 Confluence 图片 URL + 上传 Word 文档中提取图片，
    调用视觉模型识别 UI 结构，写入 RAG 供 Step3 检索。

    Args:
        image_urls: Confluence 图片 URL 列表
        sources_status: 数据源状态字典（会被原地更新 prototype_images 键）

    Returns:
        list: prototype_chunks 列表（供 Phase 3.7 RAG 构建使用）
    """
    prototype_chunks = []
    _has_vision = bool(
        getattr(config, "vision_model", "") or getattr(config, "vision_api_key", "")
    )
    if _has_vision:  # image_urls 为空也进入：Word 正文图片在识别器内部始终检查
        try:
            from src.pipeline.steps.step1.prototype_recognizer import (
                recognize_prototype_images,
                recognize_word_images,
                format_prototype_for_rag,
            )

            _vision_model = getattr(config, "vision_model", None)
            _vision_api_key = getattr(config, "vision_api_key", None)
            _vision_base_url = getattr(config, "vision_base_url", None)

            # 1. Confluence 图片识别
            if image_urls:
                _log.info(
                    "  [原型图] 开始视觉识别 %d 张 Confluence 图片...", len(image_urls)
                )
                _cf_base_url = getattr(config, "CONFLUENCE_URL", "") or ""
                _rec_results = recognize_prototype_images(
                    image_urls,
                    base_url=_cf_base_url,
                    vision_model=_vision_model,
                    vision_api_key=_vision_api_key,
                    vision_base_url=_vision_base_url,
                )
                for _rec in _rec_results:
                    if _rec.get("success") and _rec.get("data"):
                        _fmt = format_prototype_for_rag(_rec["data"])
                        if _fmt:
                            prototype_chunks.append(
                                {
                                    "text": _fmt,
                                    "source": "confluence",
                                    "url": _rec.get("url", ""),
                                }
                            )
                _ok_count = sum(1 for r in _rec_results if r.get("success"))
                _log.info(
                    "  [原型图] Confluence 图片识别: %d/%d 成功",
                    _ok_count,
                    len(_rec_results),
                )

            # 2. 上传 Word 文档图片识别
            _word_image_results = []
            _conn_w = None
            try:
                from src.core.db_manager import get_connection

                _conn_w = get_connection()
                _rows_w = _conn_w.execute(
                    "SELECT content, file_format FROM source_documents "
                    "WHERE project=%s AND doc_type='upload_word' AND category='requirement'",
                    (getattr(config, "project_name", ""),),
                ).fetchall()
                for _row_w in _rows_w or []:
                    _content_w = _row_w.get("content", "")
                    if _content_w:
                        pass  # Word 图片识别在上传时处理，此处跳过避免重复解析
            except Exception as _we:
                _log.debug("[原型图] Word 图片提取跳过: %s", str(_we)[:60])
            finally:
                if _conn_w:
                    try:
                        _conn_w.close()
                    except Exception as _close_e:
                        _log.debug("连接关闭异常: %s", _close_e)

            if prototype_chunks:
                _log.info(
                    "  [原型图] 共识别 %d 张原型图，待写入 RAG", len(prototype_chunks)
                )
                sources_status["prototype_images"] = {
                    "status": "ok",
                    "detail": "%d 张原型图已识别" % len(prototype_chunks),
                }
            else:
                sources_status["prototype_images"] = {
                    "status": "skipped",
                    "detail": "无图片可识别",
                }
        except Exception as _proto_e:
            _log.warning("  [原型图] 视觉识别异常（不阻断）: %s", str(_proto_e)[:120])
            sources_status["prototype_images"] = {
                "status": "failed",
                "detail": str(_proto_e)[:80],
            }
    else:
        sources_status["prototype_images"] = {
            "status": "skipped",
            "detail": "未配置视觉模型",
        }
    return prototype_chunks


# ── Phase 3.6: 数据源失败判定 ──────────────────────────────────────────
_STEP1_AUXILIARY_SOURCES = {"figma"}  # 设计稿是辅助源：失败降级为 warning，不阻断


def step1_source_keys() -> tuple:
    """Step1 事实源键集合（注册表内置源 + 上传文档）。

    阻断判定、进度统计等所有「Step1 有几个源/哪些源」的语义都从这里派生，
    不得各自遍历 sources_status（其中还混有 rag_coverage 等元数据键）。
    """
    from src.pipeline.datasource_registry import BUILTIN_SOURCE_KEYS

    return tuple(BUILTIN_SOURCE_KEYS) + ("upload",)


def step1_source_state(sources_status, name: str) -> str:
    """读取某数据源状态（缺失/非 dict → 空串），统一状态读取口径。"""
    st = (sources_status or {}).get(name)
    return str(st.get("status") or "") if isinstance(st, dict) else ""


def count_configured_step1_sources(sources_status) -> int:
    """已配置（非未配置/未跳过）的数据源数；阻断判定与进度推断共用。"""
    return sum(
        1
        for name in step1_source_keys()
        if step1_source_state(sources_status, name)
        not in ("", "not_configured", "skipped")
    )


def check_step1_blocking_failures(sources_status, req_text):
    """核心数据源失败 / 全部未配置且无需求正文 → 返回阻断原因（None=放行）。

    必须在 RAG 构建等副作用之前调用：失败的运行不得写入索引，否则下一次运行
    会把上次失败留下的残缺切片当成本版本的有效索引继续用。
    判定口径：用户跳过的不算失败，未配置的不算失败，辅助源失败只告警。
    """
    _blocking = [
        name
        for name in step1_source_keys()
        if name not in _STEP1_AUXILIARY_SOURCES
    ]
    failures = [
        name for name in _blocking if step1_source_state(sources_status, name) == "failed"
    ]
    auxiliary_failures = [
        name
        for name in _STEP1_AUXILIARY_SOURCES
        if step1_source_state(sources_status, name) == "failed"
    ]
    if auxiliary_failures:
        _log.warning(
            "  ⚠ 辅助数据源失败（已降级，不阻断）: %s", ", ".join(auxiliary_failures)
        )
    if failures:
        _log.error("  ❌ 已配置但拉取失败的数据源: %s", ", ".join(failures))
        return "数据源拉取失败: %s。请检查配置或取消勾选该数据源后重试" % ", ".join(
            failures
        )

    # 辅助源提供上下文但不承载需求正文：只有它可用且无需求文本时，Step2 无需求可拆
    _has_requirement_source = any(
        step1_source_state(sources_status, name)
        not in ("", "not_configured", "skipped")
        for name in _blocking
    )
    if not _has_requirement_source and not req_text:
        _log.error("  ❌ 所有数据源均未配置或被跳过，且无上传文档，阻断执行")
        return "所有数据源均未配置或被跳过，且无上传文档"
    return None


# ── Phase 3.7: RAG 增量构建 ────────────────────────────────────────────
def _build_rag_index_phase(prototype_chunks, sources_status, project, version_label):
    """Phase 3.7: 从持久化事实构建「项目 + 版本」分区的 RAG 索引。

    由持久化事实驱动 —— 始终读 source_documents(yapi_structured/database) 与
    nav_graph_nodes；有数据则必被索引，无数据则索引为空。各 builder 自身是
    "该源有没有输入"的唯一判定点，本函数不再做第二次门控。

    构建后立刻做上传文档覆盖审计（rag_coverage），把"正文是否逐字入索引"作为
    Step1 的可观测结论写入 sources_status —— 覆盖不完整时 Step1 必须阻断，
    否则下游只能基于残缺需求推理。

    Args:
        prototype_chunks: 原型图识别结果（来自 Phase 3.1，可为空）
        sources_status: 数据源状态字典（会被原地更新 rag_coverage 键）
        project: Step1 已解析出的项目名
        version_label: Step1 已解析出的版本标签

    Returns:
        None (RAG 索引构建为副作用)
    """
    _rag_data = {
        "yapi": None,
        "db_schema": "",
        "dom_snapshots": [],
    }
    _proj = project or ""
    with guard("_build_rag_index_phase: import json as _json", log=_log, level=logging.WARNING):
        import json as _json

        from src.pipeline.helpers import build_rag_index
        from src.core.db_manager import get_connection

        _conn_r = None
        try:
            _conn_r = get_connection()
            # YApi 结构化接口定义（持久化事实）
            _cur_y = _conn_r.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND version_label=%s AND doc_type='yapi_structured' "
                "ORDER BY updated_at DESC LIMIT 1",
                (_proj, version_label),
            )
            _row_y = _cur_y.fetchone() if _cur_y else None
            if _row_y and _row_y.get("content"):
                _rag_data["yapi"] = _json.loads(_row_y["content"])
            # DB Schema（_fetch_database 已持久化为 source_documents.doc_type='database'）
            _cur_d = _conn_r.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND version_label=%s AND doc_type='database' "
                "ORDER BY updated_at DESC LIMIT 1",
                (_proj, version_label),
            )
            _row_d = _cur_d.fetchone() if _cur_d else None
            if _row_d and _row_d.get("content"):
                _rag_data["db_schema"] = _row_d["content"]
            # 上传文档与 confluence 正文不在此判定：build_index 的各 builder 才是
            # "该源有没有数据"的唯一判定点，这里只负责把已持久化的数据源传下去。
        except Exception as _doc_e:
            _log.warning("[RAG] 文档型数据源读取异常(不阻塞): %s", str(_doc_e)[:120])
        finally:
            if _conn_r:
                try:
                    _conn_r.close()
                except Exception as _close_e:
                    _log.debug("连接关闭异常: %s", _close_e)

        # DOM 快照：真实产物在 nav_graph_nodes（page_dom_snapshots 已废弃，0 行）
        try:
            from src.pipeline.utils.nav_graph_read import read_dom_pages

            for _r3 in read_dom_pages(_proj, version_label):
                _rag_data["dom_snapshots"].append(
                    {
                        "page_url": _r3.get("page_url", ""),
                        "page_path": _r3.get("page_path", ""),
                        "page_title": _r3.get("page_title", ""),
                        "dom_summary": _r3.get("dom_summary", ""),
                    }
                )
        except Exception as _dom_rag_e:
            _log.error(
                "[RAG] DOM 快照读取异常(不阻塞) project=%s，RAG 将缺 DOM 维度: %s",
                _proj,
                _dom_rag_e,
                exc_info=True,
            )

        # 原型图识别结果
        if prototype_chunks:
            _rag_data["prototype_images"] = prototype_chunks
            _log.info("  [RAG] 原型图识别结果: %d chunks", len(prototype_chunks))

        # 每次 Step1 都调用 build：各 builder 自己判断"有没有输入"，无输入返回空，
        # 旧 chunks 由 build_index 的增量合并保留 —— 这里再做一次"要不要构建"的
        # 判断只会多出第二个判定点（曾导致新上传文档被门控挡在索引之外）。
        _log.info(
            "  [RAG] Step1 数据就绪(yapi=%s db=%s dom=%d)，开始增量构建 RAG 索引...",
            bool(_rag_data["yapi"]),
            bool(_rag_data["db_schema"]),
            len(_rag_data["dom_snapshots"]),
        )
        _rag_count = build_rag_index(
            yapi_data=_rag_data["yapi"],
            db_schema=_rag_data["db_schema"],
            dom_snapshots=_rag_data["dom_snapshots"],
            prototype_images=_rag_data.get("prototype_images"),
            project=_proj,
            version_label=version_label,
        )
        _log.info("  [RAG] 增量构建完成: %d chunks", _rag_count)

        # ★ 构建后覆盖自检：chunk 数不能证明"正文都在"，必须逐份核对持久化切片
        from src.pipeline.steps.step1.rag_coverage_audit import audit_upload_coverage

        sources_status["rag_coverage"] = audit_upload_coverage(_proj, version_label)
