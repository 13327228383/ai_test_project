# -*- coding: utf-8 -*-
"""关键词评分 —— 多字段加权命中。

输入：
  - 意图侧：step_desc / step_expected / 意图标题（粗略提取关键词 + 同义词扩展）
  - DOM 侧：剪枝后元素列表 list[dict]（含 text/aria-label/placeholder/id/name/class/...）

输出：
  每个元素的命中分数 + 命中字段说明（用于日志/AI 上下文）

评分规则：
  text 字段命中           +10
  aria-label 命中          +8
  placeholder 命中         +6
  id 命中                  +5
  name 命中                +4
  class 命中               +2
  href 命中                +3
  同一字段多关键词命中：分数累加（每词独立计分）
  同义词命中（非原词）：减半计分（避免同义词压过原词）

  特殊：tag 类型对动作意图的隐式加权
    意图含"输入/填写/输入框"→ tag 是 input/textarea 时 +5
    意图含"点击/按钮"        → tag 是 button/a 时 +5
    意图含"选择"             → tag 是 select 时 +5
    意图含"勾选/复选"        → tag 是 input[type=checkbox/radio] 时 +5
"""

import re
from typing import Set, Any

from ._synonyms_common import COMMON_LOOKUP
from ._synonyms_project import PROJECT_LOOKUP


# 字段权重表（哪个字段命中给多少分）
_FIELD_WEIGHTS = {
    "text": 10,
    "aria-label": 8,
    "placeholder": 6,
    "id": 5,  # 注意：DOM 元素里这是 ai-id，html_id 才是页面真实 id
    "html_id": 5,
    "name": 4,
    "href": 3,
    "class": 2,
}

# tag 隐式加权（意图关键词 → 期望的 tag 集合 → 加分）
_TAG_HINTS = [
    (
        {"输入", "填写", "填入", "录入", "fill", "type", "enter"},
        {"input", "textarea"},
        5,
    ),
    ({"点击", "click", "按钮", "button", "tap"}, {"button", "a"}, 5),
    ({"选择", "选取", "select", "choose", "pick"}, {"select"}, 5),
    (
        {"勾选", "复选", "选中", "check", "tick", "✓"},
        {"checkbox", "radio"},
        5,
    ),  # tag=input 但要看 type
    ({"上传", "upload", "选择文件"}, {"file"}, 5),  # type=file
]


def extract_keywords(text: str) -> Set[str]:
    """从一段中文/英文混合文本里粗略提取关键词。

    策略（v0.1 简单粗暴版本）：
    1. 抽连续的中文片段（2-6 字）
    2. 抽连续的英文单词（≥3 字符，过滤停用词）
    3. 全部转小写
    """
    if not text:
        return set()
    text = text.strip().lower()
    keywords: Set[str] = set()

    # 中文：2-6 字连续片段
    for m in re.finditer(r"[一-龥]{2,6}", text):
        keywords.add(m.group())
    # 中文额外：把整段中文按 2-3 字滑动窗口拆出来（处理"点击保存按钮"这种）
    chinese_seq = "".join(re.findall(r"[一-龥]+", text))
    for length in (2, 3, 4):
        for i in range(len(chinese_seq) - length + 1):
            keywords.add(chinese_seq[i : i + length])

    # 英文：≥3 字符单词
    en_stopwords = {
        "the",
        "and",
        "for",
        "are",
        "you",
        "with",
        "from",
        "this",
        "that",
        "have",
        "has",
        "was",
        "were",
        "will",
        "should",
        "can",
    }
    for m in re.finditer(r"[a-z][a-z0-9_-]{2,}", text):
        word = m.group()
        if word not in en_stopwords:
            keywords.add(word)

    return keywords


def expand_with_synonyms(keywords: Set[str]) -> tuple[Set[str], Set[str]]:
    """把关键词集合按同义词字典扩展。

    返回 (original, synonyms) 二元组——保留原始词集合，分开返回扩展词
    供评分时区分加权（同义词命中减半）。
    """
    original = set(keywords)
    synonyms: Set[str] = set()

    for kw in keywords:
        kw_low = kw.lower()
        # 通用字典扩展
        if kw_low in COMMON_LOOKUP:
            synonyms |= COMMON_LOOKUP[kw_low] - original
        # 项目字典扩展
        if kw_low in PROJECT_LOOKUP:
            synonyms |= PROJECT_LOOKUP[kw_low] - original

    return original, synonyms


def _field_value(element: dict[str, Any], field: str) -> str:
    """安全获取元素字段值并转小写字符串。"""
    v = element.get(field)
    if v is None:
        return ""
    return str(v).lower()


def _count_field_hits(field_value: str, words: Set[str]) -> int:
    """统计一个字段值里命中了多少个关键词（按出现次数累加，每词最多算 1 次）。"""
    if not field_value or not words:
        return 0
    return sum(1 for w in words if w and w in field_value)


def score_element(
    element: dict[str, Any],
    original: Set[str],
    synonyms: Set[str],
    intent_keywords: Set[str],
) -> tuple[int, list[str]]:
    """对单个元素计算总分 + 命中字段说明。

    返回 (score, hit_explanations)

    评分策略（v0.1.1 调整）：
    - 原词命中：按 _FIELD_WEIGHTS 全额加分
    - 同义词命中：**仅当该字段没有原词命中时**才加分（避免对同一字段双重计分），
      且按全额加分（不再减半）—— 因为绝大多数英文 UI 只有同义词没有原词，
      减半会误伤主流场景。
    """
    score = 0
    hits: list[str] = []

    for field, weight in _FIELD_WEIGHTS.items():
        value = _field_value(element, field)
        if not value:
            continue
        orig_hits = _count_field_hits(value, original)
        if orig_hits > 0:
            delta = orig_hits * weight
            score += delta
            hits.append(f"{field}+{delta}(原词{orig_hits})")
        else:
            # 仅在该字段没原词命中时，才考虑同义词
            syn_hits = _count_field_hits(value, synonyms)
            if syn_hits > 0:
                delta = syn_hits * weight
                score += delta
                hits.append(f"{field}+{delta}(同义{syn_hits})")

    # tag 隐式加权
    tag = _field_value(element, "tag")
    el_type = _field_value(element, "type")
    for trigger_words, expected_tags, bonus in _TAG_HINTS:
        if trigger_words & intent_keywords:
            # 意图关键词命中 → 检查 tag/type 是否匹配
            if tag in expected_tags or el_type in expected_tags:
                score += bonus
                hits.append(f"tag-bonus+{bonus}({tag}/{el_type})")
                break  # 只加一次 tag bonus

    return score, hits


def score_all(
    elements: list[dict[str, Any]],
    original: Set[str],
    synonyms: Set[str],
    intent_keywords: Set[str],
) -> list[dict[str, Any]]:
    """批量评分。返回按分数降序排列的 [{element, score, hits}, ...]。"""
    scored = []
    for el in elements:
        score, hits = score_element(el, original, synonyms, intent_keywords)
        if score > 0:
            scored.append({"element": el, "score": score, "hits": hits})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


__all__ = ["extract_keywords", "expand_with_synonyms", "score_element", "score_all"]
