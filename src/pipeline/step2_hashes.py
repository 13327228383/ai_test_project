# -*- coding: utf-8 -*-
"""Step 2 哈希工具函数。
从 step2.py 拆分出。"""

import hashlib
import re
from typing import Any


def compute_requirement_semantic_hash(text: str) -> str:
    """计算忽略 Markdown/空白/常见符号的需求语义哈希。"""
    normalized = str(text or "").lower()
    normalized = re.sub(r"```.*?```", " ", normalized, flags=re.S)
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    normalized = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", normalized)
    normalized = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", normalized)
    normalized = re.sub(
        r"[#>*_\-|:：,，.。;；!！?？()\[\]{}<>《》\"'/\\]+", " ", normalized
    )
    normalized = re.sub(r"\s+", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_requirement_part(value: Any) -> str:
    """规范化参与需求内容哈希的字段，避免空白/Markdown 噪声导致误判。"""
    text = str(value or "").lower()
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


# ── 提取逻辑指纹（跨版本缓存失效的通用判据）──────────────────────────────
# why: Step2 的 skip 守卫历史只比较**文档语义哈希**（需求正文是否变），不比较
#   **产出它的提取逻辑**。于是修复提取语义（如 infer_expected_feedback 的极性
#   判定）后，正文未变 → 守卫仍判"无变更"→ 直接复用 requirements 表里的**旧值**，
#   修复永远到不了下游。实测 fullcov_sut：2026-10-14 修正极性后未重拆，
#   9 条 expected_feedback='操作成功,成功' 的陈旧行继续污染 Step3/Step4
#   （REQ-049/050 的 operation 表达"否则客户端拦截"却配"操作成功"→ 极性矛盾 → 假 FAIL）。
# 判据：把「产出需求字段的语义函数集合」的源码指纹纳入 skip 决策 —— 逻辑变则缓存失效。
# 通用性：与业务无关；任何"派生字段 + 跨运行缓存"都需把派生器版本计入缓存键。
_AGGREGATOR_SOURCES: tuple[str, ...] = (
    "src/pipeline/steps/step2/breakdown_rules/numbered_list.py",
    "src/pipeline/steps/step2/breakdown_rules/standard_structure.py",
    "src/pipeline/steps/step2/breakdown_rules/conditional_statement.py",
    "src/pipeline/steps/step2/breakdown_rules/feature_description.py",
    "src/pipeline/steps/step2/analyzers/_fd_extraction.py",
    "src/pipeline/step3/_rule_extractor_keywords.py",
)


def compute_extraction_logic_fingerprint() -> str:
    """参与 skip 决策的「提取逻辑指纹」——源码内容哈希，逻辑变则缓存失效。

    只哈希**语义产出函数所在模块**（见 ``_AGGREGATOR_SOURCES``），不做 AST 级
    细粒度切分：粒度粗但零漏检（宁可多拆一次，不可复用陈旧值）。
    """
    import os

    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _h = hashlib.sha256()
    for _rel in _AGGREGATOR_SOURCES:
        try:
            with open(os.path.join(_root, _rel), "rb") as _f:
                _h.update(_rel.encode("utf-8"))
                _h.update(_f.read())
        except OSError:
            _h.update(_rel.encode("utf-8"))
            _h.update(b"<missing>")
    return _h.hexdigest()


def compute_requirement_structural_hash(text: str) -> str:
    """计算需求【骨架层】哈希：去格式 + 数字归一为占位符 N + 去空白。

    与 compute_requirement_semantic_hash（实质层，保留数字）配合做双层变更判定：
    - 骨架同 + 实质同 → unchanged
    - 骨架同 + 实质不同 → modified（如 "支持11类"→"支持8类"：骨架都是 "支持N类"，
      实质层 11≠8 → 命中 modified，绝不漏检数字变更）
    - 骨架不同 → modified（重写）

    数字归一仅用于骨架层判断结构是否变化；实质层（content_hash/semantic_hash）
    永远保留数字——数字是业务规则（11类→8类是砍功能），必须识别为变更。
    """
    normalized = str(text or "").lower()
    normalized = re.sub(r"```.*?```", " ", normalized, flags=re.S)
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    normalized = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", normalized)
    normalized = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", normalized)
    normalized = re.sub(
        r"[#>*_\-|:：,，.。;；!！?？()\[\]{}<>《》\"'/\\]+", " ", normalized
    )
    # ★ 骨架层核心：数字归一为占位符 N，11→8 不影响骨架判定
    normalized = re.sub(r"\d+", "N", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
