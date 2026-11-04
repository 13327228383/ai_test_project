# -*- coding: utf-8 -*-
"""主题一致性门禁（Theme Consistency Gate）—— 消除需求拆解"偏离整体需求"的增量守卫。

★ 定位：在外在锚定（source_quote 逐字校验，_fd_verification）之上，补一层
  **语义一致性**守卫。外在锚定解决"AI 胡编文档里没有的内容"；主题门禁解决
  "AI 拆出来的需求虽在文档内，但归属/语义偏离整体主题"（异常误判、越界联想）。

★ 通用性（对齐 AGENTS.md「通用、不依赖特定业务知识」）：
  - 不硬编码任何业务词库（不假设外汇/电商/外卖……）
  - 主题种子由 LLM 从当次文档动态提炼，天然适配任意领域
  - 一致性打分用语义向量（bge-large-zh-v1.5 余弦相似度），纯算法、无领域依赖

★ 不变量（不阻断）：
  - 任何环节失败（无 LLM / 无 embedding）→ 全部需求标记未评估，流程继续
  - 仅给低于阈值的需求打 out_of_scope 标记 + 附加一致分数，不改写、不删除需求
  - 低分项进 print 单列供人工审核（对齐人机协同闸门理念）

★ 流程：
  1. LLM 从文档提炼主题种子集（seed_theme，≤10 个功能主题词/短语）
  2. 计算种子 embedding + 每条需求 embedding（batch）
  3. 逐条需求取与其最相关种子的余弦相似度（max）
  4. 低于阈值 → out_of_scope=true，附 theme_similarity / matched_theme
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .constants import (
    _ai_brain,
    pipeline_config,
    _STABILITY_SEED,
    BREAKDOWN_MAX_TOKENS,
)

_log = logging.getLogger(__name__)

# ── 默认主题数量 / 需求文本截断长度 ──
_THEME_SEED_MIN = 4
_THEME_SEED_MAX = 10
_REQ_TEXT_LIMIT = 200  # 需求嵌入文本截断，控制 token 成本


# ══════════════════════════════════════════════════
# 1. LLM 提炼主题种子
# ══════════════════════════════════════════════════

_THEME_SEED_SYSTEM = (
    "你是资深软件需求分析师。给定一段软件需求文档，请提炼出该软件"
    "所涉及的核心功能主题，作为后续判断提取出的需求是否偏离整体的参照基准。\n"
    "要求：\n"
    "1. 主题应为具体功能短语或关键词，而非泛化形容词（如「交易」「订单管理」）\n"
    "2. 只提炼文档中明确出现或明确支撑的功能领域，不要臆测文档没写的内容\n"
    "3. 输出 4-10 个主题，每个 2-8 个中文字\n"
    "4. 只输出 JSON 数组，如 [\"买入\",\"卖出\",\"汇率\",\"账户管理\"]，不要任何解释文字"
)

_THEME_SEED_USER = (
    "以下是需求文档正文（可能被截断）：\n\n"
    "---\n{document}\n---\n\n"
    "请提取该软件的核心功能主题，输出 JSON 字符串数组。"
)


def _generate_theme_seeds(doc_text: str) -> list[str]:
    """用 LLM 从文档提炼主题种子集；失败返回 []（调用方降级）。"""
    doc = (doc_text or "").strip()
    if not doc:
        return []

    snippet = doc[:_REQ_TEXT_LIMIT * 10]  # 主题提炼仅需文档前部
    try:
        result = _ai_brain._call_api(
            system_prompt=_THEME_SEED_SYSTEM,
            user_message=_THEME_SEED_USER.format(document=snippet),
            temperature=0.0,  # 主题提炼要稳定
            max_tokens=1024,
            caller_name="feature_driven_theme_gate",
            task_type="feature_driven_theme_gate",
            seed=_STABILITY_SEED,
        )
        if not result.get("success"):
            _log.warning("[theme_gate] 主题种子生成失败: %s", str(result.get("error"))[:120])
            return []
        content = ""
        data = result.get("data", {})
        if isinstance(data, dict):
            content = data.get("content", "") or data.get("text", "") or ""
        elif isinstance(data, str):
            content = data

        seeds = _parse_theme_seeds(content)
        if not seeds:
            _log.warning("[theme_gate] 主题种子解析为空，降级关闭门禁")
            return []
        return seeds
    except Exception as e:
        _log.warning("[theme_gate] 主题种子生成异常(不阻断): %s", str(e)[:120])
        return []


def _parse_theme_seeds(content: str) -> list[str]:
    """从 LLM 输出解析 string 数组；支持 JSON 或 markdown 包裹。"""
    if not content or not isinstance(content, str):
        return []

    # 剥离 markdown 代码围栏
    cleaned = re.sub(r"```(?:json)?", "", content).strip()
    # 抽取第一个 [...] 段（容忍散文前缀/尾注）
    match = re.search(r"\[(.*?)\]", cleaned, re.DOTALL)
    if not match:
        return []

    try:
        arr = json.loads("[" + match.group(1) + "]")
    except Exception:
        # 尝试整段解析
        try:
            arr = json.loads(cleaned)
        except Exception:
            # 手工切分：按引号/逗号拆
            words = re.findall(r'"([^"]+)"', match.group(1))
            arr = [w.strip() for w in words if w.strip()]

    if not isinstance(arr, list):
        return []

    seeds = []
    for item in arr:
        s = str(item or "").strip()
        if s:
            seeds.append(s)
    # 限长
    if len(seeds) < _THEME_SEED_MIN:
        return []  # 过少视为不合格，降级关闭门禁
    return seeds[:_THEME_SEED_MAX]


# ══════════════════════════════════════════════════
# 2. Embedding 一致性打分
# ══════════════════════════════════════════════════


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量嵌入（复用语义去重已接通的 embedding 管道）。"""
    if not texts:
        return []
    try:
        from src.ai.router_module._router import router

        result = router.embed(texts)
        if result.get("success"):
            return result.get("data", [])
        _log.warning("[theme_gate] 嵌入调用失败: %s", str(result.get("error"))[:120])
        return None
    except Exception as e:
        _log.warning("[theme_gate] 嵌入不可用(不阻断): %s", str(e)[:120])
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _req_embed_text(req: dict[str, Any]) -> str:
    """单条需求的一致性嵌入文本：operation + expected_feedback。"""
    op = str(req.get("operation") or "").strip()
    fb = str(req.get("expected_feedback") or "").strip()
    text = f"{op}: {fb}" if fb else op
    return text[: _REQ_TEXT_LIMIT]


# ══════════════════════════════════════════════════
# 3. 主入口
# ══════════════════════════════════════════════════


def apply_theme_gate(
    requirements: list[dict[str, Any]],
    doc_text: str,
    project_name: str = "",
    version_label: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """主题一致性门禁主入口（不阻断，仅标记 + 统计）。

    校验每条需求的语义是否落在文档整体主题内，偏离者标 out_of_scope。

    返回:
        (requirements, stats)
        stats = {
            "enabled": bool,        # 是否实际执行（配置+AI+embedding均就绪）
            "threshold": float,
            "theme_seeds": [...],   # 提炼出的主题种子
            "evaluated": int,
            "out_of_scope": int,
            "flagged": [{req_id, theme_similarity, matched_theme}],
            "reason": str,          # 降级原因（enabled=False 时）
        }
    """
    threshold = pipeline_config.ai_theme_gate_threshold
    reqs = list(requirements or [])

    # 默认 stats：未启用
    stats: dict[str, Any] = {
        "enabled": False,
        "threshold": threshold,
        "theme_seeds": [],
        "evaluated": 0,
        "out_of_scope": 0,
        "flagged": [],
        "reason": "theme_gate 未启用（配置阈值 ≤ 0）",
    }

    if not threshold or threshold <= 0 or not reqs:
        return reqs, stats

    # ── 1. 主题种子 ──
    seeds = _generate_theme_seeds(doc_text)
    if not seeds:
        stats["reason"] = "主题种子生成失败，降级关闭门禁"
        return reqs, stats

    # ── 2. embedding：种子 + 需求 ──
    seed_embs = _embed_texts(seeds)
    if not seed_embs or len(seed_embs) != len(seeds):
        stats["reason"] = "主题种子嵌入不可用，降级关闭门禁"
        return reqs, stats

    # ★ 文档主题重心 = 所有主题种子向量的均值
    #   代表文档整体语义重心，比"与单个 seed 最大相似"更强判别：
    #   「买入：下单成功」是外汇交易重心附近 → 高分；
    #   「点外卖：下单成功」虽含"下单"，但整体重心在外卖 → 远离文档重心 → 低分。
    dim = len(seed_embs[0])
    doc_theme_vec = [
        sum(se[i] for se in seed_embs if se) / len(seed_embs) for i in range(dim)
    ]

    req_texts = [_req_embed_text(r) for r in reqs]
    req_embs = _embed_texts(req_texts)
    if not req_embs or len(req_embs) != len(reqs):
        stats["reason"] = "需求嵌入不可用，降级关闭门禁"
        return reqs, stats

    # ── 3. 逐条打分 ──
    flagged: list[dict[str, Any]] = []
    out_count = 0
    for i, req in enumerate(reqs):
        emb = req_embs[i]
        # matched_theme = 最近单个种子（用于可解释）；分数 = 与文档主题重心相似度
        single_sims = [
            _cosine_similarity(emb, se) for se in seed_embs if se and emb
        ]
        if not single_sims:
            continue
        matched_idx = single_sims.index(max(single_sims))
        matched_theme = seeds[matched_idx] if matched_idx < len(seeds) else ""

        best = _cosine_similarity(emb, doc_theme_vec) if emb else 0.0
        req["theme_similarity"] = round(best, 3)
        req["matched_theme"] = matched_theme
        if best < threshold:
            req["out_of_scope"] = True
            req["out_of_scope_reason"] = (
                f"与文档主题重心一致性 {best:.3f} < 阈值 {threshold}，最近主题「{matched_theme}」"
            )
            out_count += 1
            flagged.append(
                {
                    "req_id": req.get("req_id", f"REQ-{i + 1:03d}"),
                    "theme_similarity": round(best, 3),
                    "matched_theme": matched_theme,
                }
            )

    # ── 4. 统计 ──
    stats.update(
        {
            "enabled": True,
            "theme_seeds": seeds,
            "evaluated": len(reqs),
            "out_of_scope": out_count,
            "flagged": flagged[:50],  # 限长，防止超大文档刷屏
            "reason": "OK",
        }
    )

    if out_count:
        _log.info(
            "[theme_gate] 主题一致性门禁: %d 条需求中 %d 条疑似偏离主题 (阈值 %.2f)",
            len(reqs),
            out_count,
            threshold,
        )
    return reqs, stats