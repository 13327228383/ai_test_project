# -*- coding: utf-8 -*-
from __future__ import annotations

"""
RagEngine 核心类 —— 组装所有 mixin，提供主入口
"""

import math
import json
import struct
import logging

from src.core.pipeline_config import pipeline_config
from src.core.config_loader import config
from src.extensions.rag_engine.chunking_mixin import ChunkingMixin
from src.extensions.rag_engine.embedding_mixin import EmbeddingMixin
from src.extensions.rag_engine.search_mixin import SearchMixin
from src.extensions.rag_engine.persistence_mixin import PersistenceMixin

# ★ 阶段拆分(2026-07-25): build_index 内嵌闭包 + _build_figma_index 方法
#   已迁移至 _index_builders.py（§4.3 单文件≤800行）
from src.extensions.rag_engine._index_builders import (
    _build_dom_chunks as _mod_build_dom_chunks,
    _build_api_chunks as _mod_build_api_chunks,
    _build_db_chunks as _mod_build_db_chunks,
    _build_feature_chunks as _mod_build_feature_chunks,
    _build_prototype_chunks as _mod_build_prototype_chunks,
    _build_figma_index as _mod_build_figma_index,
)

_log = logging.getLogger("rag_engine")


class RagEngine(ChunkingMixin, EmbeddingMixin, SearchMixin, PersistenceMixin):
    """轻量 RAG 引擎：文档分块 + 向量嵌入 + 语义检索。"""

    # ── 类常量（提取自原魔法数字，全部走 pipeline_config 消除硬编码） ──
    _EMBED_DIM: int = pipeline_config.rag_embed_dim
    _VECTOR_SEARCH_THRESHOLD: float = pipeline_config.rag_vector_search_threshold
    _BM25_K1: float = pipeline_config.rag_bm25_k1
    _BM25_B: float = pipeline_config.rag_bm25_b
    _CONTEXT_SNIPPET_LEN: int = pipeline_config.rag_context_snippet_len
    _SIMILAR_REQ_SNIPPET_LEN: int = pipeline_config.rag_similar_req_snippet_len
    _SIMILAR_REQ_TOP_N: int = pipeline_config.rag_similar_req_top_n
    _EMBED_TEXT_MAX_LEN: int = pipeline_config.rag_embed_text_max_len
    _EMBED_CHUNK_PREVIEW: int = pipeline_config.rag_embed_chunk_preview
    _EMBED_CHUNK_MAX_LEN: int = pipeline_config.rag_embed_chunk_max_len
    _EMBED_LOG_INTERVAL: int = pipeline_config.rag_embed_log_interval
    _KEYWORD_MAX_COUNT: int = pipeline_config.rag_keyword_max_count
    _UPLOAD_SEGMENT_MAX_LEN: int = pipeline_config.rag_upload_segment_max_len
    _API_META_TRUNCATE: int = pipeline_config.rag_api_meta_truncate
    _DB_COL_LIMIT: int = pipeline_config.rag_db_col_limit
    _DB_COL_FORMAT_LIMIT: int = pipeline_config.rag_db_col_format_limit
    _PAGE_ELEMENT_LIMIT: int = pipeline_config.rag_page_element_limit
    _KEYWORD_NGRAM_SIZES: tuple = (2, 3)  # 算法常量，保持硬编码
    _MAX_LOAD_CHUNKS: int = pipeline_config.rag_max_load_chunks
    _MAX_TOP_K: int = pipeline_config.rag_max_top_k
    _EMBED_MODEL_CANDIDATES: tuple = (
        "paraphrase-multilingual-MiniLM-L12-v2",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "all-MiniLM-L6-v2",
        "sentence-transformers/all-MiniLM-L6-v2",
        "distiluse-base-multilingual-cased-v2",
    )

    def __init__(self, root_dir=None):
        self._chunks: list[dict] = []
        self._use_embeddings = False  # 是否成功使用 embedding API
        self._bm25_index: dict[str, dict] = {}  # 回退方案
        self._detected_metric: tuple | None = None  # auto 推断结果缓存
        # ★ 全 MySQL：向量与文本一律走 rag_chunks 表，不再使用 ChromaDB 文件存储。
        # root_dir 参数保留向后兼容（旧测试可能传入 Path），但不再用于磁盘存储。

    # ★ _parse_db_tables 已迁移至 ChunkingParseMixin（chunking_mixin_mixins/_parse.py）
    #   MRO 自动解析到增强版（优先实时 information_schema 查询，回退文本正则）

    # ── 向量辅助：紧凑存储 + 点积 ────────────────────────────────
    @staticmethod
    def _vector_to_blob(vec: list) -> bytes:
        if not vec:
            return b""
        return struct.pack("<" + "f" * len(vec), *[float(v) for v in vec])

    @staticmethod
    def _blob_to_vector(blob: bytes, dim: int = 0) -> list:
        if not blob:
            return []
        count = dim if dim > 0 else len(blob) // 4
        return list(struct.unpack("<" + "f" * count, blob))

    @staticmethod
    def _l2_norm(vec: list) -> float:
        if not vec:
            return 0.0
        return math.sqrt(sum(v * v for v in vec))

    @staticmethod
    def _dot_product(a: list, b: list) -> float:
        """对归一化向量，数学上等价于余弦，但省一次 sqrt 和除法。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    # ═══════════════════════════════════════════
    # 索引构建
    # ═══════════════════════════════════════════
    # ★ 架构债清理(2026-07-25): _build_figma_index 方法已迁移至
    #   _index_builders._build_figma_index（§4.3 单文件≤800行）
    #   build_index 通过模块级函数 _mod_build_figma_index(self) 调用

    def build_index(
        self,
        requirements: list[dict] = None,
        yapi_specs: dict = None,
        db_schema: str = "",
        dom_snapshots: list[dict] = None,
        prototype_images: list[dict] = None,
        force_refresh: bool = False,
        use_embeddings: bool = True,
        user_id: str = "",
        project: str = "",
        version_label: str = "",
    ) -> int:
        """
        构建 RAG 索引（增量更新 + 全量重建支持）。在 Step1 拉取数据后调用一次。

        参数:
            requirements: 需求列表（已不再用于构建 RAG，保留参数兼容）
            yapi_specs: YAPI 接口文档
            db_schema: 数据库 Schema
            dom_snapshots: DOM 快照列表（来自 Step1 DOM 爬取）
            prototype_images: 原型图识别结果列表（来自 Step1 视觉识别）
            force_refresh: True=全量重建（先清空所有旧数据）; False=增量更新（仅更新变化的数据源）
            use_embeddings: True=尝试调用 embedding API 构建向量索引（回退到 BM25）;
                           False=强制使用 BM25 检索，不调用 embedding API
            project: 项目名（调用方显式传入时以其为准，避免各处重推上下文）
            version_label: 版本标签（同上）；索引按「项目 + 版本」分区读写

        返回: chunk 总数
        ★ v4: 增量更新 —— 只有有输入数据的数据源才重建其 chunks；
               无输入数据的数据源保留旧数据（除非 force_refresh）。
        ★ v3: 7 个数据源并行分块 + 批量 INSERT 持久化。
        ★ v5: 移除 requirement chunks —— 需求原文通过 slice_id 直接查询 requirement_slices 表，
               RAG 只保留 API/DB/DOM/Figma/Feature/Uploads chunks。
        ★ 2026-07-13: 新增 prototype_images 数据源（视觉模型识别的原型图 UI 结构）。
        """
        project = project or self._get_project_name()
        version_label = version_label or self._get_project_version()
        scope_type, scope_id = self._project_scope(project)

        if not project or not version_label:
            _log.warning(
                "[RagEngine] 项目(%s)或版本(%s)上下文缺失，放弃构建索引",
                project or "?",
                version_label or "?",
            )
            return 0

        # 不再在入口做"有没有新数据就跳过"的判断：每个 builder 自己就是"该源
        # 有没有输入"的唯一判定点（无输入返回空列表），旧 chunks 由下方增量合并
        # 保留。入口再判一次只会多出第二个判定点——uploads/features/figma 的输入
        # 只在 DB 里，按调用参数判"无新数据"会把新上传文档挡在索引之外。
        _log.info(
            "[RagEngine] 构建索引（%s模式）...",
            "全量重建" if force_refresh else "增量更新",
        )

        if force_refresh:
            _log.info(
                "[RagEngine] force_refresh=True，清空当前版本旧 chunks (user=%s, version=%s)",
                user_id or "全部",
                version_label or "?",
            )
            conn = None
            try:
                from src.core.db_manager import get_connection

                conn = get_connection()
                if user_id:
                    conn.execute(
                        "DELETE FROM rag_chunks WHERE scope_type=%s AND scope_id=%s "
                        "AND version_label=%s AND user_id=%s",
                        (scope_type, scope_id, version_label, user_id),
                    )
                else:
                    conn.execute(
                        "DELETE FROM rag_chunks WHERE scope_type=%s AND scope_id=%s "
                        "AND version_label=%s",
                        (scope_type, scope_id, version_label),
                    )
                conn.commit()
                _log.info("[RagEngine] 已清空旧 chunks")
            except Exception as e:
                _log.warning("[RagEngine] 清空旧 chunks 失败: %s", str(e)[:80])
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as e:
                        _log.exception("[RagEngine] 关闭连接失败: %s", e)

        # ── 并行执行 section：全部提交，由各 builder 自行判定有无输入 ──
        # ★ 阶段拆分(2026-07-25): 5 个闭包已迁移至 _index_builders.py 模块级函数
        #   闭包本质是模块级函数+self参数，参考 _ai_loop_phases.py 模式
        from concurrent.futures import ThreadPoolExecutor, as_completed

        section_chunks: list = []
        futures = {}

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures[executor.submit(_mod_build_dom_chunks, self, dom_snapshots)] = "dom"
            futures[executor.submit(_mod_build_api_chunks, self, yapi_specs)] = "api"
            futures[executor.submit(_mod_build_db_chunks, self, db_schema)] = "db"
            futures[
                executor.submit(_mod_build_prototype_chunks, self, prototype_images)
            ] = "prototype_images"
            # features/uploads/figma 在 executor 工作线程执行，thread-local 的
            # _current_project()/_current_version() 在该线程为空，只能回退 config
            # 静态值 → 查 0 行。此处显式下传入口线程已解析出的 project/version_label。
            futures[
                executor.submit(_mod_build_feature_chunks, self, project, version_label)
            ] = "features"
            futures[
                executor.submit(
                    self._load_uploaded_file_chunks,
                    project=project,
                    version_label=version_label,
                )
            ] = "uploads"
            futures[
                executor.submit(_mod_build_figma_index, self, project, version_label)
            ] = "figma"

            for future in as_completed(futures):
                section = futures[future]
                try:
                    result = future.result()
                    if result:
                        section_chunks.extend(result)
                        _log.info(
                            "[RagEngine] ✓ %s 分块: %d chunks", section, len(result)
                        )
                except Exception as e:
                    _log.warning("[RagEngine] ✗ %s 分块失败: %s", section, str(e)[:120])

        # ── 增量更新：加载无输入数据源的旧 chunks（force_refresh 时跳过）──
        if not force_refresh:
            _log.info("[RagEngine] 增量更新：加载无输入数据源的旧 chunks...")
            self._load_chunks(project, version_label)  # 先加载该版本分区的现有 chunks
            # 合并：新 chunks 覆盖旧 chunks，无输入数据源的旧 chunks 保留
            new_chunk_ids = {c.get("chunk_id", "") for c in section_chunks}
            for old_chunk in self._chunks:
                old_id = old_chunk.get("chunk_id", "")
                if old_id and old_id not in new_chunk_ids:
                    section_chunks.append(old_chunk)
            _log.info("[RagEngine] 增量合并完成: %d chunks", len(section_chunks))

        self._chunks = section_chunks

        # ★ P2-5: 跨文档语义去重（去重后再建索引，保持索引精简）
        _log.info("[RagEngine] 开始跨文档去重 (共 %d chunks)...", len(self._chunks))
        before_dedup = len(self._chunks)
        self._chunks = self._deduplicate_chunks(self._chunks)
        after_dedup = len(self._chunks)
        if before_dedup > after_dedup:
            _log.info(
                "[RagEngine] 去重: %d → %d chunks (移除 %d 个重复)",
                before_dedup,
                after_dedup,
                before_dedup - after_dedup,
            )
        _log.info("[RagEngine] 跨文档去重完成")

        # 构建 BM25 索引（总是可用）
        _log.info("[RagEngine] 开始构建 BM25 索引...")
        self._build_bm25_index()
        _log.info("[RagEngine] BM25 索引构建完成")

        # ★ 显式控制是否构建向量索引
        if use_embeddings:
            _log.info("[RagEngine] 开始构建向量嵌入...")
            self._try_build_embeddings(project, version_label)
            _log.info("[RagEngine] 向量嵌入构建完成")
        else:
            self._use_embeddings = False
            _log.info("  [RagEngine] Embedding 已禁用，使用 BM25 关键词检索")

        # 持久化（MySQL rag_chunks 表，批量 INSERT）
        _log.info("[RagEngine] 开始持久化到 MySQL...")
        self._save_to_db(user_id=user_id, project=project, version_label=version_label)
        _log.info("[RagEngine] 持久化完成")
        # 重置 metric 检测：数据变了，下次检索时重新推断最佳方法
        self._detected_metric = None
        # 预热检测一次（构建完就跑一下，便于日志观察）
        if self._chunks:
            try:
                self._detected_metric = self._detect_best_metric()
            except Exception as _e:
                _log.debug("[RagEngine] 预热 metric 检测失败: %s", _e)
        _log.error(
            "[RagEngine] 索引构建完成: %d 个 chunk (embedding=%s)"
            % (len(self._chunks), "✓" if self._use_embeddings else "✗")
        )
        return len(self._chunks)

    # ═══════════════════════════════════════════
    # 检索
    # ═══════════════════════════════════════════

    def retrieve(
        self,
        requirement: dict,
        top_k: int = 5,
        retrieve_types: list = None,
    ) -> dict[str, list[dict]]:
        """
        根据需求描述检索最相关的上下文。

        参数:
            requirement:    {"req_id": "...", "module": "...", "description": "..."}
            top_k:          每个类别返回的最大数量
            retrieve_types: 要检索的类别列表，None=全部。
                           可选: api_endpoints, db_tables, figma_pages,
                                 figma_nodes, figma_components, similar_requirements

        返回:
            {"api_endpoints": [...], "db_tables": [...], "figma_pages": [...], "similar_requirements": [...]}
        """
        top_k = self._normalize_top_k(top_k)
        query = f"{requirement.get('module', '')} {requirement.get('description', '')}".strip()
        self_req_id = requirement.get("req_id", "")

        # ★ B2: 多 query 扩展（默认关闭）
        try:
            queries = self._expand_query(query)
        except Exception:
            queries = [query]
        if not queries:
            queries = [query]

        ALL_RESULT_KEYS = [
            "api_endpoints",
            "db_tables",
            "figma_pages",
            "figma_nodes",
            "figma_components",
            "similar_requirements",
            "dom_snapshots",
            "uploaded_docs",
            "prototype_images",
        ]
        # ★ Step3 优化：可按需只检索指定类型，避免与 trace_links 重复检索
        if retrieve_types is not None:
            active_keys = [k for k in retrieve_types if k in ALL_RESULT_KEYS]
        else:
            active_keys = list(ALL_RESULT_KEYS)
        results = {k: [] for k in ALL_RESULT_KEYS}

        type_map = {
            "api": "api_endpoints",
            "db_table": "db_tables",
            "db_column": "db_tables",
            "db_sample": "db_tables",
            "figma_page": "figma_pages",
            "figma_node": "figma_nodes",
            "figma_component": "figma_components",
            "requirement": "similar_requirements",
            "dom": "dom_snapshots",
            "prototype_image": "prototype_images",
            "upload_req": "uploaded_docs",
            "upload_dict": "uploaded_docs",
            "upload_api": "uploaded_docs",
            "upload_design": "uploaded_docs",
            "upload_data": "uploaded_docs",
            "upload_ddl": "uploaded_docs",
        }
        # 只检索活跃类型
        active_type_map = {ct: rk for ct, rk in type_map.items() if rk in active_keys}

        # 预留 rerank 时取更多候选
        try:
            from src.core.pipeline_config import pipeline_config

            rerank_enabled = bool(getattr(pipeline_config, "rag_enable_rerank", False))
        except Exception:
            rerank_enabled = False
        per_query_k = top_k * 3 if rerank_enabled else top_k

        for chunk_type, result_key in active_type_map.items():
            merged: dict[str, dict] = {}
            for q in queries:
                try:
                    sub = self._search(q, chunk_type, per_query_k)
                except Exception as _se:
                    _log.debug("[RagEngine] sub_search失败(用空): %s", str(_se)[:50])
                    sub = []
                for item in sub or []:
                    cid = item.get("chunk_id") or id(item)
                    cur = merged.get(cid)
                    if cur is None:
                        merged[cid] = dict(item)
                    else:
                        # 分数取 max
                        try:
                            if float(item.get("score", 0)) > float(cur.get("score", 0)):
                                cur["score"] = item.get("score")
                        except Exception as _e:
                            _log.debug("score + score: %s", _e, exc_info=True)
            candidates = list(merged.values())
            # ★ self-exclusion：排除需求自身（需求 X 的 chunk_id 是 req:{X}）
            if chunk_type == "requirement" and self_req_id:
                candidates = [
                    c for c in candidates if c.get("chunk_id") != f"req:{self_req_id}"
                ]
            # 排序：以 score 降序为主
            try:
                candidates.sort(
                    key=lambda x: float(x.get("score", 0) or 0), reverse=True
                )
            except Exception as _e:
                _log.debug("score + score + score: %s", _e, exc_info=True)
            # 可选 rerank
            candidates = self._maybe_rerank(query, candidates, top_k)
            if candidates:
                results[result_key] = candidates[:top_k]

        return results

    def _normalize_top_k(self, top_k: int) -> int:
        """限制 top_k 的合法范围，避免一次检索返回过多内容。"""
        try:
            value = int(top_k)
        except Exception:
            value = 5
        if value <= 0:
            return 1
        return min(value, self._MAX_TOP_K)

    def retrieve_and_resolve(
        self,
        requirement: dict,
        top_k: int = 10,
        return_refs: bool = False,
        retrieve_types: list = None,
    ):
        """★ v4: RAG 检索 + EntityResolver + SemanticRenderer 一站式管道。

        替代旧的 retrieve() + format_retrieval_context() 两步流程。

        流程:
            1. RAG 检索 → 返回 chunks
            2. 收集 entity_refs（有则用，无则从 type+chunk_id 构建）
            3. EntityResolver.resolve() → 获取完整源数据
            4. SemanticPatternRenderer.render() → 渲染为语义文本

        Args:
            requirement: 需求字典 {"req_id": "...", "module": "...", "description": "..."}
            top_k: 检索 top-k 个 chunk
            return_refs: ★ S3-012: 若为 True，返回 (text, entity_refs) 元组，
                         entity_refs 用于写入 rag_usage_log 表
            retrieve_types: ★ 按操作类型过滤 RAG 类型，避免全量 dump。
                            None=全部，可选: api_endpoints, db_tables, figma_pages,
                            figma_nodes, figma_components, similar_requirements,
                            dom_snapshots, uploaded_docs

        Returns:
            return_refs=False: 渲染后的语义文本（str）
            return_refs=True:  (text, entity_refs) 元组，entity_refs 为 RAG 来源列表
        """
        # 1. RAG 检索
        retrieved = self.retrieve(
            requirement, top_k=top_k, retrieve_types=retrieve_types
        )
        if not retrieved:
            return ("", []) if return_refs else ""

        # 2. 收集 entity_refs（★ 2026-07-12: 保留 RAG 分数 + 按操作关键词加权）
        entity_refs = []
        _op_keywords = []
        _operation = (
            requirement.get("operation", "") if isinstance(requirement, dict) else ""
        )
        if _operation:
            # 提取操作关键词（与 _contexts.py 的 n-gram 策略一致）
            import re as _re_op

            _cleaned = _re_op.sub(r"[（(].*?[）)]", "", _operation).strip()
            _op_keywords = [_cleaned] if len(_cleaned) >= 2 else []
            for _n in (2, 3):
                for _i in range(len(_cleaned) - _n + 1):
                    _op_keywords.append(_cleaned[_i : _i + _n])

        for section_name, chunks in retrieved.items():
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                _rag_score = float(chunk.get("score", 0) or 0)
                _chunk_text = str(
                    chunk.get("text", "") or chunk.get("content", "") or ""
                )

                # ★ 关键词加权：chunk 文本命中操作关键词 → 加分
                _kw_bonus = 0
                if _op_keywords and _chunk_text:
                    _kw_hits = sum(
                        1 for kw in _op_keywords if kw.lower() in _chunk_text.lower()
                    )
                    _kw_bonus = _kw_hits * 0.05  # 每个命中 +0.05
                _combined_score = _rag_score + _kw_bonus

                refs = chunk.get("entity_refs")
                if not refs:
                    # 退化：从 type + chunk_id 动态构建
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
                if refs:
                    for ref in refs:
                        ref["_section"] = section_name
                        ref["_chunk_id"] = chunk.get("chunk_id", "")
                        ref["_score"] = _combined_score
                        ref["_kw_bonus"] = _kw_bonus
                        ref["_content"] = _chunk_text  # ★ L7 Cross-Encoder 需要
                    entity_refs.extend(refs)

        # ★ 2026-07-12: 按综合分数过滤，只保留 top N 个 entity_refs（每类型最多 3 个）
        #   原理：RAG 返回 top_k=5 个 chunk，其中低分 chunk 是噪音。
        #   按 combined_score 排序，每类型取 top 3，减少 40% 低质量 chunk。
        if entity_refs and len(entity_refs) > 6:
            from collections import defaultdict

            _by_type = defaultdict(list)
            for ref in entity_refs:
                _by_type[ref.get("_section", "unknown")].append(ref)
            _filtered = []
            for _sec, _refs in _by_type.items():
                _refs.sort(key=lambda r: r.get("_score", 0), reverse=True)
                _filtered.extend(_refs[:3])  # 每类型最多 3 个
            _log.info(
                "[retrieve_and_resolve] 分数过滤: %d → %d entity_refs (%d 类型)",
                len(entity_refs),
                len(_filtered),
                len(_by_type),
            )
            entity_refs = _filtered

        if not entity_refs:
            text = self.format_retrieval_context(retrieved)
            return (text, []) if return_refs else text

        # 3. EntityResolver 精确查询（使用全局实例，跨调用复用缓存）
        from src.extensions.rag_engine.entity_resolver import get_global_resolver

        resolver = get_global_resolver(
            project=getattr(self, "_project", None),
            version_id=getattr(self, "_version_id", None),
        )
        resolved = resolver.resolve(entity_refs)

        # 4. SemanticRenderer 渲染
        from src.extensions.rag_engine.semantic_renderer import SemanticPatternRenderer

        renderer = SemanticPatternRenderer()
        semantic_text = renderer.render(resolved)

        if not semantic_text:
            text = self.format_retrieval_context(retrieved)
            return (text, entity_refs) if return_refs else text

        return (semantic_text, entity_refs) if return_refs else semantic_text

    def format_retrieval_context(self, retrieved: dict) -> str:
        """
        将检索结果格式化为可注入 AI prompt 的文本。
        替代当前的全量注入方式，减少 prompt 长度 50-80%。

        ⚠️ v4 deprecated: 请使用 retrieve_and_resolve() 替代。
        本方法保留向后兼容，后续版本可能移除。
        """
        lines = []

        if retrieved.get("api_endpoints"):
            lines.append("## 相关 API 接口（语义检索 Top-N）")
            lines.append("以下是与当前需求最相关的 API 接口，请优先参考：")
            for c in retrieved["api_endpoints"]:
                lines.append(f"- {c['text'][:self._CONTEXT_SNIPPET_LEN]}")
            lines.append("")

        if retrieved.get("db_tables"):
            lines.append("## 相关数据库表（语义检索 Top-N）")
            lines.append("以下是与当前需求最相关的数据库表：")
            for c in retrieved["db_tables"]:
                lines.append(f"- {c['text'][:self._CONTEXT_SNIPPET_LEN]}")
            lines.append("")

        if retrieved.get("figma_pages"):
            lines.append("## 相关页面（语义检索 Top-N）")
            lines.append("以下是与当前需求最相关的页面：")
            for c in retrieved["figma_pages"]:
                lines.append(f"- {c['text'][:self._CONTEXT_SNIPPET_LEN]}")
            lines.append("")

        if retrieved.get("figma_nodes"):
            lines.append("## 相关交互节点（语义检索 Top-N）")
            lines.append("以下是与当前需求最相关的页面内交互元素：")
            for c in retrieved["figma_nodes"]:
                lines.append(f"- {c['text'][:self._CONTEXT_SNIPPET_LEN]}")
            lines.append("")

        if retrieved.get("figma_components"):
            lines.append("## 相关设计组件（语义检索 Top-N）")
            lines.append("以下是与当前需求相关的可复用设计组件：")
            for c in retrieved["figma_components"]:
                lines.append(f"- {c['text'][:self._CONTEXT_SNIPPET_LEN]}")
            lines.append("")

        if retrieved.get("similar_requirements"):
            lines.append("## 关联需求（同项目兄弟需求参考）")
            lines.append("当前需求的兄弟需求，可参考其测试用例覆盖方向保持一致性：")
            for c in retrieved["similar_requirements"][: self._SIMILAR_REQ_TOP_N]:
                lines.append(f"- {c['text'][:self._SIMILAR_REQ_SNIPPET_LEN]}")
            lines.append("")

        return "\n".join(lines) if lines else ""

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def has_embeddings(self) -> bool:
        return self._use_embeddings


# 单例（全走 MySQL rag_chunks，无需 project_root）
# ★ 方案C 改造：rag_engine 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

rag_engine = PerProjectRegistry(RagEngine)
