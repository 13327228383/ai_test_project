# -*- coding: utf-8 -*-
"""需求→菜单纯算法匹配器（v5.14, 2026-08-04 通用化）。

从 Step4 _exploratory.py 提取的纯算法三级融合匹配（LCS+Jaccard+关键词重叠），
零业务词依赖，跨语言/系统/行业通用。供 Step3 导航解析器与 Step4 浏览器导航复用。

设计初衷(2026-07-31)：需求词汇和 UI 导航标签存在天然语义 gap，单一匹配策略
无法覆盖所有场景。LCS 处理"烟叶画像数据"→"烟叶画像"，Jaccard 处理
"配方管理"→"配方列表"。

通用性：纯算法零依赖，不绑定任何具体系统/业务领域。
"""

__all__ = ["lcs_similarity", "jaccard_char_similarity", "match_module_to_menu"]


def lcs_similarity(a: str, b: str) -> float:
    """最长公共子序列相似度（0~1，越高越相似）。

    纯算法零依赖，处理任意部分重叠（"烟叶画像数据" vs "烟叶画像"）。
    """
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    return prev[n] / max(m, n)


def jaccard_char_similarity(a: str, b: str) -> float:
    """字符级 Jaccard 相似度（0~1）。

    处理"配方管理" vs "配方列表"（共同字符"配方"，不同后缀）。
    """
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    union = set_a | set_b
    return (len(set_a & set_b) / len(union)) if union else 0.0


def match_module_to_menu(module: str, menu_items: list) -> tuple:
    """三级融合匹配——包含 > LCS > Jaccard。

    参数:
        module:     需求模块/描述文本（中文）
        menu_items: 菜单标题列表

    返回:
        (best_match_text, best_score, match_stage)
        stage ∈ {"exact_contain", "lcs_jaccard", "none"}
    """
    if not module or not menu_items:
        return None, 0.0, "none"

    _clean_mod = (
        module.replace("、", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("（", "")
        .replace("）", "")
    )

    _best_match = None
    _best_score = 0.0
    _best_stage = "none"

    for _item in menu_items:
        if not _item:
            continue
        _clean_item = _item.replace(" ", "")

        # 阶段1: 精确包含（最高优先级）
        if _clean_mod in _clean_item or _clean_item in _clean_mod:
            _len_penalty = abs(len(_clean_mod) - len(_clean_item)) / max(
                len(_clean_mod), len(_clean_item), 1
            )
            _score = 1.0 - _len_penalty * 0.2
            if _score > _best_score:
                _best_score = _score
                _best_match = _item
                _best_stage = "exact_contain"
            continue

        # 阶段2: LCS 相似度（权重 0.55）
        _lcs = lcs_similarity(_clean_mod, _clean_item)

        # 阶段3: Jaccard 字符相似度（权重 0.25）
        _jac = jaccard_char_similarity(_clean_mod, _clean_item)

        _mod_chars = set(_clean_mod) - _MENU_COMMON_TOKENS
        _item_chars = set(_clean_item) - _MENU_COMMON_TOKENS
        _kw_overlap = (
            (len(_mod_chars & _item_chars) / max(len(_mod_chars | _item_chars), 1))
            if _mod_chars
            else 0.0
        )

        # 融合分数
        _score = _lcs * 0.55 + _jac * 0.25 + _kw_overlap * 0.20

        if _score > _best_score:
            _best_score = _score
            _best_match = _item
            _best_stage = "lcs_jaccard"

    return _best_match, _best_score, _best_stage


# ── 词表唯一源（提升自 src/pipeline/utils/menu_matcher.py，下游 import 引用，勿重抄）──
# 阶段4: 关键词重叠（权重 0.20）——通用词过滤后的关键词匹配
_MENU_COMMON_TOKENS = {
    "数据",
    "管理",
    "信息",
    "系统",
    "配置",
    "设置",
    "查询",
    "统计",
    "分析",
    "处理",
    "显示",
    "展示",
    "操作",
    "功能",
    "服务",
    "模块",
}
