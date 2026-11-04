# -*- coding: utf-8 -*-
"""本地语义引擎 — 云端 LLM 不可用时的确定性语义能力（stdlib-only）。

职责: 接管 SemanticMixin 四个 LLM 方法在熔断分支的空窗（返回 None/UNKNOWN），
用通用文本特征给出与 LLM 版**输出格式一致**的判定，调用方无需区分数据来源。

通用性: 纯文本特征（字符集 / 子串 / 编辑距离 / 既有同义簇 COMMON_LOOKUP），
零业务词表、零外部依赖、跨系统/行业。方法契约对齐 LLM 版:
  best_candidate  ~ _semantic_menu_match  (返回 (文案, score) 或 None)
  rank_candidates ~ _semantic_menu_rank   (返回排序后文案列表)
  match_element   ~ _semantic_element_match(返回 {index, score} 或 None)
  assert_result   ~ _semantic_assertion   (返回 {verdict, confidence, evidence})

判定原则（防假 PASS 红线）:
  - 本地引擎只做"证据充分才判定"，模棱两可一律返回 None/UNKNOWN，
    由调用方既有 DOM/关键词兜底继续，不越权下结论。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
import re
from typing import Any, Optional

_log = logging.getLogger(__name__)

# 短词对长词的字符覆盖率达到该比例即判语义相关（UX02 '资产类别'→'分类' 实证 0.5）
_RELATED_COVERAGE = 0.5
# best_candidate 的命中阈值（相似度综合分）
_MATCH_THRESHOLD = 0.42

_RE_NON_CHAR = re.compile(r"[^\w\u4e00-\u9fff]", re.UNICODE)
_RE_ASCII_WORD = re.compile(r"[a-z][a-z0-9_-]*")


def _norm(text: str) -> str:
    """归一化: 小写 + 去标点/空格。保留中文与英文数字，便于字符级比较。"""
    s = re.sub(r"[\s_\-/\\（）()【】\[\]：:，,。.、]", "", (text or "").lower())
    return s


def _char_set(text: str) -> set[str]:
    """连续中文按单字、英文按整词拆成特征集（跨语言通用）。"""
    s = _norm(text)
    features: set[str] = set()
    cn = re.findall(r"[\u4e00-\u9fff]", s)
    features.update(cn)
    for w in _RE_ASCII_WORD.findall(s):
        if len(w) >= 2:
            features.add(w)
    return features


def _synonym_cluster(word: str) -> set[str] | None:
    """查询既有同义簇（复用 _synonyms_common.COMMON_LOOKUP，禁止新增词表）。"""
    try:
        from src.pipeline.steps.step4.browser_core._smart_locator._synonyms_common import (  # noqa: E501
            COMMON_LOOKUP,
        )
    except Exception:
        return None
    for w in (word, word.lower()):
        if w in COMMON_LOOKUP:
            return COMMON_LOOKUP[w]
        # 中文双字词尝试整体查（"分类"整体不在簇但"类别"簇内有近义词时取整簇）
        if len(w) >= 2 and w[:2] in COMMON_LOOKUP:
            return COMMON_LOOKUP[w[:2]]
    return None


def _cluster_hits_text(cluster: set[str], text: str) -> bool:
    """意图侧同义簇内任一词元出现在候选文本中即判语义相关。

    短词元限制：中文 >=2 字、英文 >=4 字符，避免 'x'/'no' 这类极短词在
    长文案里被误命中（防假 PASS 红线：宁可不命中，不可错配）。
    """
    if not cluster or not text:
        return False
    for w in cluster:
        if len(w) < 2:
            continue
        _is_cn = any("\u4e00" <= ch <= "\u9fff" for ch in w)
        if (_is_cn and len(w) >= 2) or (not _is_cn and len(w) >= 4):
            if w in text:
                return True
    return False


def similarity(a: str, b: str) -> float:
    """两个文本的语义相似度 0~1（子串 > 同义簇 > 字符覆盖 > 编辑距离）。"""
    if not a or not b:
        return 0.0
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    score = 0.0
    # ① 子串包含（双向）——最常见的"业务前缀长词 vs 字段短词"（资产类别 vs 类别）
    if na in nb or nb in na:
        score = max(score, 0.95)
    # ② 同义簇命中（如 手机/电话 等既有簇）
    ca, cb = _synonym_cluster(na), _synonym_cluster(nb)
    if ca and cb and (na in cb or nb in ca or (ca & cb)):
        score = max(score, 0.9)
    elif ca and not cb and _cluster_hits_text(ca, nb):
        # 控件文案多为复合词（"按名称搜索"）取不到簇，须用意图侧簇词扫描候选，
        # 否则意图为纯动作词时语义簇恒失效（只剩碰巧是字面子串才命中）。
        score = max(score, 0.85)
    # ③ 短词字符对长词的覆盖率（处理 类别/分类 这类同核不同序词对）。
    #    仅当词对含中文时启用：中文单字是语义原子，英文必须整词才具语义，
    #    否则 'name'↔'assets' 会因字符集近似误判相关。
    sa, sb = set(na), set(nb)
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    _has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in na + nb)
    if _has_cn and short:
        union = short & long_
        score = max(score, (len(union) / len(short)) * 0.85)
    # ⑤ 属性核（中文表单字段名 = 域前缀 + 属性后缀：名称/类别/数量…）。
    #    去掉操作词首2字符前缀后，属性核在候选中命中即显著加权，用于区分
    #    '资产名称'↔'资产类别'这类共享域前缀、属性后缀不同的字段对（实测
    #    字符集覆盖对两者同为0.425，无法分离；'按名称搜索'因含属性核'名称'
    #    应胜过类别选择框）。
    _cn_only = [ch for ch in na if "\u4e00" <= ch <= "\u9fff"]
    if len(_cn_only) >= 4 and len(na) > 2:
        _kernel = na[2:]
        if _kernel:
            score = max(score, similarity(_kernel, nb) * 0.9)
    # ④ 编辑距离（处理轻微笔误/单点差异）
    with guard("similarity: from difflib import SequenceMatcher", log=_log):
        from difflib import SequenceMatcher

        ratio = SequenceMatcher(None, na, nb).ratio()
        score = max(score, ratio * 0.9)
    return score


def is_semantically_related(a: str, b: str, coverage: float = _RELATED_COVERAGE) -> bool:
    """布尔判据（label 兜底等调用点复用）：短词字符覆盖率 >= 阈值即相关。

    '资产类别' vs '分类': 短词集合 {分,类} ∩ {资,产,类,别} = {类} → 0.5 ✓
    '资产类别' vs '名称': 交集空 → 0 ✗（防御性，防误命中）
    """
    if not a or not b:
        return False
    return similarity(a, b) >= coverage * 0.85


def best_candidate(operation: str, candidates: list[str]) -> tuple[str, float] | None:
    """从候选中选出语义最相关文案（对齐 _semantic_menu_match 返回语义）。

    取最高分候选；仅当 score 明显优势（>= 次优 + 0.15，防多候选均沾）且过阈值才返回。
    全不达标返回 None，调用方继续既有关键词兜底。
    """
    _cands = [c for c in (candidates or []) if c and str(c).strip()]
    if not _cands or not operation:
        return None
    scored = [(similarity(operation, c), str(c)) for c in _cands]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_text = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if (
        top_score >= _MATCH_THRESHOLD
        and top_score - second_score >= 0.15
        and isinstance(top_text, str)
    ):
        return top_text, top_score
    return None


def rank_candidates(operation: str, candidates: list[str]) -> list[str]:
    """按语义相似度降序排列候选（对齐 _semantic_menu_rank 返回列表）。"""
    _cands = [c for c in (candidates or []) if c and str(c).strip()]
    if not _cands or not operation:
        return list(candidates or [])
    scored = sorted(
        ((similarity(operation, c), str(c)) for c in _cands),
        key=lambda x: x[0],
        reverse=True,
    )
    return [t for _, t in scored]


def match_element(
    operation: str, elements: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """从元素列表中匹配语义最相关元素（对齐 _semantic_element_match）。

    返回 {element, score, hit_count}；全不达标返回 None。
    """
    _els = [e for e in (elements or []) if isinstance(e, dict)]
    if not _els or not operation:
        return None
    best: tuple[float, dict[str, Any]] = (0.0, {})
    for el in _els:
        texts = [
            str(el.get(k) or "") for k in ("text", "ariaLabel", "title", "placeholder")
        ]
        s = max((similarity(operation, t) for t in texts if t), default=0.0)
        if s > best[0]:
            best = (s, el)
    if best[0] >= _MATCH_THRESHOLD:
        return {
            "element": best[1],
            "score": int(best[0] * 10),
            "hit_count": 1,
        }
    return None


_VERDICT_CUE = {
    "PASS": ("成功", "成功!", "操作成功", "保存成功", "创建成功", "提交成功"),
    "FAIL": ("失败", "错误", "异常", "拒绝", "不存在", "已存在", "无效", "不允许"),
}


def assert_result(
    operation: str,
    expected_feedback: str,
    page_snapshot_text: str,
    actions_taken: list[dict] | None = None,
) -> dict[str, Any]:
    """本地操作结果断言（对齐 _semantic_assertion 返回格式）。

    保守判定: 仅当期望反馈词（或其同义簇）在快照中出现 → PASS；
    期望为成功语义但快照含明确失败词 → FAIL；其余 UNKNOWN（不越权）。
    """
    exp = str(expected_feedback or "").strip()
    snap = str(page_snapshot_text or "")[:2000]
    if not exp:
        return {"verdict": "UNKNOWN", "confidence": 0.0, "evidence": "期望反馈为空"}
    exp_norm = _norm(exp)
    # 期望词在快照（归一化+字符级）中的命中
    exp_set = _char_set(exp)
    snap_norm = _norm(snap)
    hit_ratio = 0.0
    if exp_set and snap_norm:
        hit_ratio = len(exp_set & set(snap_norm)) / len(exp_set)
    has_fail_cue = any(
        c in snap_norm for c in _VERDICT_CUE["FAIL"]
    )
    has_pass_cue = any(
        c in snap_norm for c in _VERDICT_CUE["PASS"]
    )
    # 期望本身带失败语义（"应提示错误"）→ 以失败 cue 为准
    exp_is_fail = any(c in exp_norm for c in ("失败", "错误", "拒绝", "提示"))
    if exp_is_fail:
        if has_fail_cue:
            return {"verdict": "PASS", "confidence": 0.8, "evidence": "快照含期望失败提示"}
        return {"verdict": "UNKNOWN", "confidence": 0.0, "evidence": "未见失败提示"}
    if has_fail_cue and any(
        c in exp_norm for c in ("成功", "保存", "创建", "提交", "完成")
    ):
        return {"verdict": "FAIL", "confidence": 0.85, "evidence": "期望成功但快照含失败词"}
    if hit_ratio >= 0.5 and has_pass_cue:
        return {"verdict": "PASS", "confidence": 0.85, "evidence": "期望词与成功提示同时命中"}
    if hit_ratio >= 0.8:
        return {"verdict": "PASS", "confidence": 0.7, "evidence": "期望词高占比命中快照"}
    return {"verdict": "UNKNOWN", "confidence": 0.0, "evidence": "本地判定证据不足"}


__all__ = [
    "similarity",
    "is_semantically_related",
    "best_candidate",
    "rank_candidates",
    "match_element",
    "assert_result",
]