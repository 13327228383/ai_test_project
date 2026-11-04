# -*- coding: utf-8 -*-
"""Step1 文档采集与 RAG 索引 — 算法规则元信息清单（只读，供"算法规则清单"展示）。

原清单(`src/backend/routes/case_rules/_routes.py`)只覆盖 step2/3/4/5，Step1 的
多源采集与 RAG chunks 构建算法从未被收录。本文件补齐 Step1 侧缺失规则。

原则（与 Step4 `_rule_meta.py` 保持一致）：
  1. 只收录代码中真实存在、可解释、有明确实现入口的函数；
  2. 每条 `rule_id` 均标注真实实现路径，保证"描述与源码同步、零硬编码脱节"；
  3. 纯函数、无副作用、可独立单测。
"""
from __future__ import annotations

from typing import Any

# ═══════════════════════════════════════════════════════════════
# Step1 RAG chunks 相关规则（来自 src/extensions/rag_engine/* 的真实实现）
# build_rag_index → rag_engine.build_index（pipeline/helpers.py:222 调度）
# ═══════════════════════════════════════════════════════════════
_RAG_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "step1.rag_parse",
        "name": "多源解析抽取",
        "description": "把需求/YApi 接口/DB 表结构原型 DOM 快照等原始输入解析为结构化"
        "实体(接口端点及参数、数据表及字段、页面结构)。实现: "
        "chunking_mixin_mixins._parse(_parse_yapi_endpoints/_parse_db_tables/"
        "_parse_db_columns_batch)。",
        "stage": "rag",
        "priority": 70,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_keywords",
        "name": "中文分词与关键词提取",
        "description": "对文本切片做中文分词、去停用词并提取关键词，供关键词检索与语义"
        "相似度计算。实现: chunking_mixin_mixins._keywords"
        "(_tokenize_chinese/_extract_keywords)。",
        "stage": "rag",
        "priority": 69,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_chunk",
        "name": "语义切片与格式化",
        "description": "按实体边界将内容切成 RAG chunks(requirement/api/db/page 多种"
        "类型)，并统一格式化为检索文本。实现: chunking_mixin_mixins._segment."
        "extract_feature_points 与 _format(_format_api_chunk/_format_db_chunk/"
        "_format_page_chunk)。",
        "stage": "rag",
        "priority": 68,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_dedup",
        "name": "切片去重",
        "description": "按内容/哈希去重重复切片，避免重复声道与检索噪声。实现: "
        "chunking_mixin_mixins._dedup._deduplicate_chunks。",
        "stage": "rag",
        "priority": 67,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_embed",
        "name": "向量嵌入",
        "description": "为切片生成语义向量(本地磁盘模型/Ollama/云端 API 多通道，"
        "失败降级本地 fallback)，并用余弦相似度度量语义距离。实现: "
        "embedding_mixin(_try_embed_disk/_try_embed_ollama/_try_embed_api/"
        "_fallback_embed/_cosine_sim)。",
        "stage": "rag",
        "priority": 66,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_index",
        "name": "RAG 索引构建",
        "description": "Step1 完成后、Step2 拆解前统一构建 RAG 索引，聚合多源切片并"
        "持久化，保证 Step2/Step3 检索数据可用。实现: "
        "pipeline/helpers.build_rag_index → rag_engine.build_index。",
        "stage": "rag",
        "priority": 65,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_retrieve",
        "name": "混合检索",
        "description": "根据需求/操作语义召回相关切片，融合关键词与向量结果并归一化"
        "top_k。实现: engine_core.retrieve / retrieve_and_resolve。",
        "stage": "rag",
        "priority": 60,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_rerank",
        "name": "结果重排序",
        "description": "对召回结果做 MMR 多样性 + 文本相似度 + LLM 判定重排，抑制重复"
        "证据。实现: rag_rerank(_mmr_diversity/_text_similarity/_llm_judge/"
        "_judge_batch)。",
        "stage": "rag",
        "priority": 58,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_filter",
        "name": "证据过滤与上下文压缩",
        "description": "按实体前缀分派、recomp 压缩、CRAG 分解裁剪冗余切块，输出精简"
        "检索上下文。实现: rag_filter(filter_rag_chunks/_recomp_compress/"
        "_crag_decompose/_cross_encoder_rerank)。",
        "stage": "rag",
        "priority": 57,
        "version": "1.0",
    },
    {
        "rule_id": "step1.rag_resolve",
        "name": "实体解析",
        "description": "把检索命中的实体引用解析为真实 API 端点/DB 表/Figma 节点的"
        "可执行目标，打通「文档→动作」闭环。实现: entity_resolver.resolve"
        "(_resolve_api_endpoints/_resolve_db_tables/_resolve_figma_nodes)。",
        "stage": "rag",
        "priority": 56,
        "version": "1.0",
    },
]


def get_rule_meta_list() -> list[dict[str, Any]]:
    """获取 Step1 全部算法规则元信息（文档采集 + RAG chunks 构建/检索）。"""
    return list(_RAG_RULES)


__all__ = ["get_rule_meta_list"]