# -*- coding: utf-8 -*-
"""流水线自动调参模块（从 pipeline_config.py 拆分，§4.3 单文件≤800行）。

职责：
  - _detect_model_window: 从当前 AI 模型推测上下文窗口大小
  - _detect_signals: 一次调用获取所有数据可用性信号（Step2/Step3 共用）
  - _bound: 有界整数裁剪
  - auto_tune_step3: 零输入自调优 Step3 最佳配置默认值
  - auto_tune_step2: 零输入自调优 Step2 最佳配置默认值

通用性：纯算法计算，基于模型窗口 + 数据可用性信号，适用于所有项目/行业。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _detect_model_window() -> int:
    """共享检测：从当前 AI 模型推测上下文窗口大小。检测不到返回 128000。"""
    with guard("_detect_model_window: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config

        model_name = (getattr(config, "ai_model", "") or "").lower()
        if "deepseek" in model_name:
            return 128000
        if "claude" in model_name or "opus" in model_name:
            return 200000
        if "gpt" in model_name or "openai" in model_name:
            return 128000
    return 128000  # 通用保守值


def _detect_signals() -> dict:
    """共享检测：一次调用获取所有数据可用性信号。Step2/Step3 共用。"""
    signals = {
        "model_window": _detect_model_window(),
        "has_rag": False,
        "has_trace": False,
        "has_figma": False,
        "has_db": False,
        "cpu_count": 4,
    }
    # RAG
    with guard("_detect_signals: from src.extensions.rag_engine impor", log=_log):
        from src.extensions.rag_engine import rag_engine

        signals["has_rag"] = rag_engine and rag_engine.chunk_count > 0
    # trace_links
    with guard("_detect_signals: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            row = conn.execute("SELECT 1 FROM trace_links LIMIT 1").fetchone()
            signals["has_trace"] = row is not None
        finally:
            with guard("_detect_signals: conn.close()", log=_log):
                conn.close()
            release_connection(conn)
    # CPU
    import os as _os

    signals["cpu_count"] = getattr(_os, "cpu_count", lambda: 4)()

    return signals


def detect_project_signals(project: str = "") -> dict:
    """按项目检测可用性与规模信号（替代全局 _detect_signals 的项目无关部分）。

    返回字段：
      - model_window: 模型上下文窗口
      - num_reqs: 该项目需求数（Step3 主驱动）
      - num_docs: 该项目源文档数
      - doc_total_chars: 该项目源文档总字符数
      - has_trace / has_figma / has_db: 按项目检测的数据可用性
      - has_rag: 全局 RAG 引擎（rag_engine 是全局索引，无法精确按项目分）
      - cpu_count
    project 为空时回退到全局 _detect_signals（保持旧行为）。
    """
    import os as _os

    signals = {
        "project": project or "",
        "model_window": _detect_model_window(),
        "num_reqs": 0,
        "num_docs": 0,
        "doc_total_chars": 0,
        "has_trace": False,
        "has_figma": False,
        "has_db": False,
        "has_rag": False,
        "cpu_count": getattr(_os, "cpu_count", lambda: 4)(),
    }
    proj = (project or "").strip()
    if not proj:
        g = _detect_signals()
        for k in ("model_window", "has_rag", "has_trace", "has_figma", "has_db", "cpu_count"):
            if k in g:
                signals[k] = g[k]
        return signals
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            p = proj
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM requirements WHERE project=%s", (p,)
            ).fetchone()
            if r:
                signals["num_reqs"] = int(r["c"] or 0)
            r = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(char_count),0) AS s "
                "FROM source_documents WHERE project=%s", (p,)
            ).fetchone()
            if r:
                signals["num_docs"] = int(r["c"] or 0)
                signals["doc_total_chars"] = int(r["s"] or 0)
            r = conn.execute(
                "SELECT 1 FROM trace_links WHERE project=%s LIMIT 1", (p,)
            ).fetchone()
            signals["has_trace"] = r is not None
            r = conn.execute(
                "SELECT 1 FROM figma_nodes WHERE project=%s LIMIT 1", (p,)
            ).fetchone()
            signals["has_figma"] = r is not None
            # has_db：项目级 DB 知识（source_db_schema 不存在则回退 trace 推导）
            try:
                r = conn.execute(
                    "SELECT 1 FROM source_db_schema WHERE project=%s LIMIT 1", (p,)
                ).fetchone()
                signals["has_db"] = r is not None
            except Exception:
                signals["has_db"] = signals["has_trace"]
            try:
                from src.extensions.rag_engine import rag_engine

                signals["has_rag"] = bool(
                    rag_engine and getattr(rag_engine, "chunk_count", 0) > 0
                )
            except Exception:
                signals["has_rag"] = False
        finally:
            with guard("detect_project_signals: conn.close()", log=_log):
                conn.close()
            release_connection(conn)
    except Exception as _e:
        _log.debug("项目信号检测失败，降级全局信号: %s", str(_e)[:80])
        with guard("detect_project_signals: g = _detect_signals()", log=_log):
            g = _detect_signals()
            for k in ("has_rag", "has_trace", "has_figma", "has_db", "cpu_count"):
                if k in g:
                    signals[k] = g[k]
    return signals


def _bound(v, lo, hi):
    return max(lo, min(hi, int(v)))


def auto_tune_step3(
    model_window: int = None,
    num_reqs: int = 1,
    has_rag: bool = False,
    has_trace: bool = False,
    has_db_schema: bool = False,
) -> dict:
    """零输入自调优：基于系统状态计算 Step3 最佳配置默认值。

    用户不需要知道填什么值——算法自动检测模型窗口、数据可用性、
    需求数量，计算各上下文类型的最优字符分配和检索深度。

    返回推荐配置 dict，可直接作为 CONFIG_SCHEMA 中 step3 分组的
    recommended 值注入前端。

    所有信号无法检测时降级为保守默认值，不影响系统运行。
    """
    # 1. 模型窗口（检测不到 → 128K 保守值）
    if model_window is None:
        model_window = _detect_model_window()

    # 2. 总上下文预算 = 模型窗口的 15%（留 85% 给 prompt 模板 + AI 输出）
    total_budget = int(model_window * 0.15)

    # 3. 每需求预算（需求越多，单需求越省；最少 500 字符兜底）
    per_req = int(total_budget / max(1, num_reqs))
    per_req = max(500, per_req)

    # 4. RAG top_k（trace 数据越丰富，RAG 需求量越低）
    if not has_rag:
        rag_top_k = 0
    elif has_trace:
        # trace 已提供精确 API/表/页面，RAG 只补充语义
        rag_top_k = _bound(5 - num_reqs / 10, 2, 5)
    else:
        rag_top_k = _bound(7 - num_reqs / 8, 3, 5)

    # 5. 上下文份额分配（根据数据可用性动态调整比例）
    if has_trace and has_rag:
        ui_s, tr_s, sc_s = 0.25, 0.18, 0.18  # 双全：均衡
    elif has_trace:
        ui_s, tr_s, sc_s = 0.00, 0.30, 0.25  # 只有 trace：偏重精确关联 + schema
    elif has_rag:
        ui_s, tr_s, sc_s = 0.40, 0.00, 0.22  # 只有 RAG：偏重语义 UI + schema
    else:
        ui_s, tr_s, sc_s = 0.00, 0.00, 0.30  # 都没有：最大偏重 schema

    # 6. 各上下文限制 = 每需求预算 × 份额（有界）
    limits = {
        "step3_ctx_ui": _bound(per_req * ui_s, 200, 4000),
        "step3_ctx_trace": _bound(per_req * tr_s, 200, 4000),
        "step3_ctx_schema": _bound(per_req * sc_s, 200, 4000),
        "step3_ctx_data_dict": _bound(per_req * 0.12, 200, 3000),
        "step3_ctx_project": _bound(per_req * 0.08, 200, 2000),
        "step3_ctx_rules": _bound(per_req * 0.05, 100, 1500),
        "step3_ctx_few_shot": _bound(per_req * 0.04, 100, 1500),
        "step3_ctx_feedback": _bound(per_req * 0.03, 100, 1000),
    }

    # 7. 并行 workers（官方 API 高并发，代理保守防排队限流）
    from src.core.config_loader import config as _at_cfg

    _base_url = str(getattr(_at_cfg, "ai_base_url", "") or "").lower()
    _OFFICIAL_HOSTS = [
        "api.deepseek.com",
        "api.openai.com",
        "api.anthropic.com",
        "integrate.api.nvidia.com",
        "generativelanguage.googleapis.com",
    ]
    _is_official = any(h in _base_url for h in _OFFICIAL_HOSTS)
    import os as _os

    cpu_count = getattr(_os, "cpu_count", lambda: 4)()
    if _is_official:
        workers = _bound(num_reqs / 2 + 1, 3, min(15, cpu_count or 10))
    else:
        workers = 2  # 代理/网关默认保守，防止排队限流

    result = {
        "step3_rag_top_k": rag_top_k,
        "step3_rag_retrieve_types": (
            "figma_nodes,figma_components,similar_requirements" if has_rag else ""
        ),
        "step3_parallel_workers": workers,
    }
    result.update(limits)
    result["_meta"] = {
        "detected_model_window": model_window,
        "per_req_budget_chars": per_req,
        "total_context_budget": total_budget,
        "num_reqs": num_reqs,
        "has_rag": has_rag,
        "has_trace": has_trace,
        "has_db_schema": has_db_schema,
        "tuned_at": "",
    }
    return result


def auto_tune_step2(
    doc_total_chars: int = 0,
    model_window: int = None,
    has_figma: bool = False,
    has_db: bool = False,
    doc_has_headings: bool = False,
    num_reqs: int = 0,
    num_docs: int = 0,
    has_trace: bool = False,
) -> dict:
    """零输入自调优：基于文档大小和模型窗口计算 Step2 最佳配置默认值。

    主驱动信号是文档总字符数（Step1 产出），辅助信号为 Figma/DB 可用性。
    公式：分片大小 = 模型窗口 × 安全系数；分片数 = ceil(文档大小/分片大小)。

    返回推荐配置 dict，包含所有 step2_* 项的推荐值。
    """
    if model_window is None:
        model_window = _detect_model_window()

    # ── 1. 分片策略（文档驱动的核心计算）──
    safety_factor = 0.60  # 60% 给输入，40% 留给 system prompt + 输出
    shard_max_chars = _bound(model_window * safety_factor, 3000, 50000)

    # 大文档阈值 = 模型窗口 25%（超过则启用分片）
    large_doc_threshold = _bound(model_window * 0.25, 5000, 100000)

    # 分片重叠 = 分片大小的 1.5%
    shard_overlap = _bound(shard_max_chars * 0.015, 50, 2000)

    # 分片数：综合「文档规模」与「文档数量」双维度（项目级差异化核心）
    if doc_total_chars > 0:
        doc_based = _bound(
            -(-doc_total_chars // max(1, shard_max_chars - shard_overlap)),  # ceil 除法
            1,
            20,
        )
    else:
        doc_based = 2  # 无文档信息时保守值
    # 并行分片数跟随源文档数量（多文档项目分片更多）
    shard_count = _bound(max(2, max(num_docs, doc_based)), 2, 20)

    # ── 2. 模块数量（需求数 + 文档密度 共同驱动，项目级差异化）──
    # 以「需求数」与「文档/2000字符」的较大者为基准，避免小文档被 clamp 到地板值
    if num_reqs > 0 or doc_total_chars > 0:
        base_modules = max(num_reqs, doc_total_chars / 2000)
        max_modules = _bound(base_modules, 2, 50)
        # 如果有标题结构，结构提取通常比全文拆解多 25% 模块
        structural_max = _bound(
            max_modules * (1.25 if doc_has_headings else 1.0), 2, 50
        )
    else:
        max_modules = 15
        structural_max = 20

    # ── 3. 每模块上下文预算 ──
    # Step2 每个模块需要：module_text（主要）+ 辅助数据（UI/DB知识）
    # 辅助数据占每模块总预算的 20%
    per_module_budget = _bound(shard_max_chars * 0.20, 500, 10000)

    # UI 相关（Figma 数据）
    if has_figma:
        ui_full = _bound(per_module_budget * 0.25, 500, 8000)  # 全文拆解路径
        ui_shard = _bound(per_module_budget * 0.18, 200, 5000)  # 分片拆解路径
        ui_reduce = _bound(ui_full * 0.17, 100, 3000)  # 低重叠时精简量
    else:
        ui_full = 0
        ui_shard = 0
        ui_reduce = 0

    # DB 知识
    if has_db:
        knowledge_max = _bound(per_module_budget * 0.35, 500, 10000)
    else:
        knowledge_max = 0

    # 快速模式输入上限
    quick_mode_max = _bound(model_window * 0.25, 500, 10000)

    # 模块超时（保守 300s，检测不到更精确的模型速度）
    timeout = 300

    # 发现阶段 max_tokens
    discover_tokens = _bound(model_window * 0.12, 256, 16384)

    result = {
        "step2_shard_max_chars": shard_max_chars,
        "step2_shard_overlap_chars": shard_overlap,
        "step2_shard_max_count": shard_count,
        "step2_shard_model_safety_factor": round(safety_factor, 2),
        "step2_large_doc_threshold": large_doc_threshold,
        "step2_max_modules": max_modules,
        "step2_structural_max_modules": structural_max,
        "step2_ui_full_max_chars": ui_full,
        "step2_ui_shard_max_chars": ui_shard,
        "step2_ui_reduce_chars": ui_reduce,
        "step2_knowledge_max_chars": knowledge_max,
        "step2_quick_mode_max_chars": quick_mode_max,
        "step2_per_module_timeout_seconds": timeout,
        "step2_discover_max_tokens": discover_tokens,
    }
    result["_meta"] = {
        "detected_model_window": model_window,
        "doc_total_chars": doc_total_chars,
        "shard_max_chars": shard_max_chars,
        "has_figma": has_figma,
        "has_db": has_db,
        "doc_has_headings": doc_has_headings,
        "tuned_at": "",
    }
    return result
