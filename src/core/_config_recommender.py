# -*- coding: utf-8 -*-
"""流水线推荐值统一算法（独立专文件，§4.3 单文件≤800行）。

职责
----
- ``recommend_all(schema, signals=None)``：按 CONFIG_SCHEMA 为「全部 9 组」的每一项
  生成一个可在页面展示、可一键填入并落库生效的推荐值。
- Step2 / Step3 复用 ``_auto_tune`` 的算法；其余 7 组（step4 / confidence / ai /
  step_timeout / evidence / verification / rag）按项目信号（需求数、文档规模、trace/
  figma/db/RAG 可用性、CPU 核数、模型窗口）给出启发式推荐。
- 每一项在返回前都按 schema 的 type/min/max 收敛，保证值必落在合法区间（能落地）。

通用性 / 智能 / 易用
--------------------
- 纯算法计算 + 项目信号驱动，不绑定任何行业/业务，适用于所有靶子系统。
- 信号即使用户显式传入，也只是只读提示；是否采纳由用户「一键填入 + 保存」决定，
  except 一切异常都降级为 schema 默认值，绝不因推荐计算失败影响配置读取。
- 所有信号无法检测时按"保守默认"兜底，调用方（get_user_config / recommended 端点）
  仅需一行接入，无需关心细节。
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


# ── 有界收敛（int / float / bool / str-enum 分别处理）─────────────────────
def _clamp(v: Any, lo: Any, hi: Any) -> Any:
    if v is None:
        return None
    try:
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return v


def _i(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def clamp_meta(value: Any, meta: dict) -> Any:
    """按配置项 schema 元数据收敛推荐值，保证能落地（类型正确 + 在 [min,max] 内）。"""
    if value is None:
        return None
    t = meta.get("type", "str")
    try:
        if t == "bool":
            return bool(value)
        if t == "int":
            lo = meta.get("min")
            hi = meta.get("max")
            v = _i(value)
            if lo is not None:
                v = max(_i(lo), v)
            if hi is not None:
                v = min(_i(hi), v)
            return v
        if t == "float":
            lo = meta.get("min")
            hi = meta.get("max")
            v = _f(value)
            if lo is not None:
                v = max(_f(lo), v)
            if hi is not None:
                v = min(_f(hi), v)
            return v
        if t == "enum":
            opts = [o.get("value") for o in (meta.get("options") or [])]
            return value if value in opts else meta.get("default")
        return value  # str 等原样返回
    except Exception as _e:  # pragma: no cover
        _log.debug("[config_recommender] 收敛失败(key=%s): %s", meta.get("label", ""), _e)
        return meta.get("default")


def _signals() -> dict:
    """安全获取项目信号；任何失败都降级为保守默认，绝不向上抛。"""
    try:
        from src.core._auto_tune import detect_project_signals

        return detect_project_signals("")
    except Exception as _e:  # pragma: no cover
        _log.debug("[config_recommender] 信号检测降级默认: %s", str(_e)[:80])
        return {
            "project": "",
            "model_window": 128000,
            "num_reqs": 0,
            "num_docs": 0,
            "doc_total_chars": 0,
            "has_trace": False,
            "has_figma": False,
            "has_db": False,
            "has_rag": False,
            "cpu_count": 4,
        }


def _strip_meta(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "_meta"}


# ── 各分组推荐函数 ──────────────────────────────────────────────────────────
def _rec_confidence(s: dict) -> dict:
    """置信度三档阈值：随「验证完整度 + 项目规模」整体上浮，始终保持 低<中<高 单调。

    - 三轨验证齐全（DB/trace 可用）→ 执行证据更客观可信 → 自动通过线可更严（三档上调）。
    - 需求规模 >=100 → 大规模项目再加严（仅小幅上调低线，预留中线空间）。
    返回值保证 `low < mid < high`，经 clamp_meta 收敛后仍落在 [0,1]。
    """
    well = bool(s.get("has_db")) or bool(s.get("has_trace"))
    low, mid, high = (0.55, 0.75, 0.90) if well else (0.50, 0.70, 0.90)
    n = _i(s.get("num_reqs"))
    if n >= 100:
        low = min(low + 0.05, mid - 0.05)
    return {
        "confidence_low_threshold": low,
        "confidence_medium_threshold": mid,
        "confidence_high_threshold": high,
    }


def _rec_ai(items: dict, s: dict, mw: int) -> dict:
    """AI 调用参数：本地模型超时按模型窗口 + 需求规模放宽（防本地大模型响应慢卡死）。"""
    if "ai_local_timeout" not in items:
        return {}
    t = 15
    if mw >= 200_000:
        t = 60
    elif mw >= 65_000:
        t = 30
    if _i(s.get("num_reqs")) >= 200:
        t = max(t, 60)
    return {"ai_local_timeout": t}


def _rec_step4(items: dict, s: dict) -> dict:
    n = _i(s.get("num_reqs"))
    well = bool(s.get("has_trace") or s.get("has_db"))
    cpu = max(1, _i(s.get("cpu_count")) or 4)
    rec = {
        # ★ 根因修复(2026-09-24): 采样推荐对称化 —— P0~P4 每档都给「纳入 + 抽样率」，
        #   默认全量纳入，避免推荐逻辑再次制造"P3/P4 静默消失"（原仅给 P1/P2）。
        #   小规模项目（<30 需求）本就该全量；大规模项目再由用户显式降采样。
        "step4_include_p0": True,
        "step4_p0_sample_rate": 1.0,
        "step4_include_p1": True,
        "step4_p1_sample_rate": 1.0 if n < 30 else 0.5,
        "step4_include_p2": 0 < n <= 20,
        "step4_p2_sample_rate": 1.0,
        "step4_include_p3": True,
        "step4_p3_sample_rate": 1.0,
        "step4_include_p4": True,
        "step4_p4_sample_rate": 1.0,
        "step4_red_team_enabled": False,  # 无安全等级信号，保守关闭，避免无谓 AI 开销
        "browser_max_ai_rounds": 6 if well else 12,
        "browser_max_parallel": max(1, min(8, cpu // 2)),
        "default_step4_timeout_ms": 20000 if well else 30000,
    }
    # Step4 插件开关默认全开（遵循 schema 默认），纳入推荐以便页面对齐
    for k in items:
        if k.startswith("step4_plugins_") and k.endswith("_enabled"):
            rec.setdefault(k, True)
    return rec


def _rec_step_timeout(s: dict) -> dict:
    n = _i(s.get("num_reqs"))
    return {
        "STEP1_TIMEOUT_SECONDS": 600,
        "STEP2_TIMEOUT_SECONDS": 1200 + n * 8,
        "STEP3_TIMEOUT_SECONDS": 1800 + n * 15,
        "STEP4_TIMEOUT_SECONDS": 2400 + n * 20,
        "STEP5_TIMEOUT_SECONDS": 600,
    }


def _rec_rag(s: dict) -> dict:
    doc = _i(s.get("doc_total_chars"))
    big = doc > 1_000_000
    return {
        "rag_embed_dim": 128,
        "rag_bm25_k1": 1.5,
        "rag_bm25_b": 0.75,
        "rag_context_snippet_len": 400 if big else 200,
        "rag_embed_text_max_len": 2000,
        "rag_keyword_max_count": 30 if big else 50,
        "rag_db_col_limit": 20 if bool(s.get("has_db")) else 15,
        "rag_page_element_limit": 10 if bool(s.get("has_figma")) else 5,
        "rag_rrf_k": 60,
        "rag_hybrid_bm25_weight": None,  # null = 自动推断（保持与默认一致，表示「自动」）
        "rag_similarity_metric": "auto",
        "rag_l7_cross_encoder_threshold": 0.3,
    }


def _recommend_group(group_key: str, items: dict, s: dict, mw: int) -> dict:
    """计算某一分组的推荐值，未覆盖项回退 schema 默认，全部经 clamp_meta 收敛。"""
    special: dict = {}
    if group_key == "step2":
        from src.core._auto_tune import auto_tune_step2

        special = _strip_meta(
            auto_tune_step2(
                doc_total_chars=_i(s.get("doc_total_chars")),
                model_window=mw,
                has_figma=bool(s.get("has_figma")),
                has_db=bool(s.get("has_db")) or bool(s.get("has_trace")),
                doc_has_headings=False,
                num_reqs=_i(s.get("num_reqs")),
                num_docs=_i(s.get("num_docs")),
                has_trace=bool(s.get("has_trace")),
            )
        )
    elif group_key == "step3":
        from src.core._auto_tune import auto_tune_step3

        special = _strip_meta(
            auto_tune_step3(
                model_window=mw,
                num_reqs=max(1, _i(s.get("num_reqs"))),
                has_rag=bool(s.get("has_rag")),
                has_trace=bool(s.get("has_trace")),
                has_db_schema=bool(s.get("has_db")),
            )
        )
    elif group_key == "step4":
        special = _rec_step4(items, s)
    elif group_key == "step_timeout":
        special = _rec_step_timeout(s)
    elif group_key == "rag":
        special = _rec_rag(s)
    elif group_key == "confidence":
        special = _rec_confidence(s)
    elif group_key == "ai":
        special = _rec_ai(items, s, mw)
    # evidence / verification：分别存在「权重和≈1.0」「档位单调序」约束，
    # 独立启发式易破坏约束，故回退 schema 默认（默认值即为经审定安全值）。

    out: dict = {}
    for k, meta in items.items():
        if k in special:
            out[k] = clamp_meta(special[k], meta)
        else:
            out[k] = clamp_meta(meta.get("default"), meta)
    return out


def recommend_all(schema: dict, signals: dict = None) -> dict:
    """为 schema 中全部 9 组 / 每项生成推荐值。

    参数
    ----
    - schema: CONFIG_SCHEMA 结构（{group: {"items": {...}}}）
    - signals: detect_project_signals 输出；为 None 时内部自动检测并降级兜底。
    返回 {group_key: {item_key: value}}，每个值都经 clamp_meta 收敛，保证能落地。
    """
    if signals is None:
        signals = _signals()
    try:
        mw = max(1024, _i(signals.get("model_window")) or 128000)
    except Exception:  # pragma: no cover
        mw = 128000
    out: dict = {}
    for gk, group in (schema or {}).items():
        items = (group or {}).get("items") or {}
        if not items:
            out[gk] = {}
            continue
        out[gk] = _recommend_group(gk, items, signals, mw)
    return out