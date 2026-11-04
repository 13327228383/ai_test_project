# -*- coding: utf-8 -*-
"""
semantic_dom_tree.utils._region — 关键词提取、聚类与区域构建
"""

import logging
import re
from collections import Counter
from typing import Any

from ._classify import (
    _TYPE_BUTTON,
    _TYPE_INPUT,
    _TYPE_LINK,
    _TYPE_OTHER,
    _TYPE_TABLE,
    _classify,
    _get_h,
    _get_text,
    _get_y,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 关键词提取（中文 2-gram + 英文词）
# ---------------------------------------------------------------------------
def _extract_keywords(texts: list[str], top_n: int = 5) -> list[str]:
    try:
        counter: Counter = Counter()
        for t in texts:
            if not t:
                continue
            tl = t.lower()
            # 英文词
            for m in re.finditer(r"[a-zA-Z][a-zA-Z0-9_]{2,}", tl):
                counter[m.group(0)] += 1
            # 中文 2-gram
            for block in re.findall(r"[\u4e00-\u9fff]{2,}", tl):
                for i in range(len(block) - 1):
                    counter[block[i : i + 2]] += 1
                # 也把整块作为候选（如果较短）
                if 2 <= len(block) <= 6:
                    counter[block] += 1
        # 过滤掉太常见的停用模式（简单处理）
        stop_prefix = ("的", "了", "在", "是", "一个", "这", "那")
        filtered = [
            (w, c)
            for w, c in counter.most_common(top_n * 4)
            if not any(w.startswith(s) for s in stop_prefix)
        ]
        return [w for w, _ in filtered[:top_n]]
    except Exception as e:
        _log.warning("semantic_dom_tree._extract_keywords 异常 %s", e)
        return []


# ---------------------------------------------------------------------------
# 聚类与区域构建
# ---------------------------------------------------------------------------
def _cluster_regions(elements: list[Any]) -> list[list[Any]]:
    """按 y 坐标聚类成多个区域。"""
    try:
        if not elements:
            return []

        # 按 y 升序，y 相同再按 x（没 x 就保持原顺序）
        sorted_elems = sorted(
            elements,
            key=lambda el: (_get_y(el), 0),
        )

        # 计算平均高度，作为切分阈值的基础（平均高度 / 3）
        heights = [_get_h(el) for el in sorted_elems]
        avg_h = (sum(heights) / len(heights)) if heights else 24.0
        threshold = max(avg_h / 3.0, 12.0)  # 至少 12px

        # 相邻 y 差 > threshold 则切一刀
        regions: list[list[Any]] = []
        current: list[Any] = [sorted_elems[0]]
        last_y = _get_y(sorted_elems[0])

        for el in sorted_elems[1:]:
            y = _get_y(el)
            if (y - last_y) > threshold and len(current) > 0:
                regions.append(current)
                current = [el]
            else:
                current.append(el)
            last_y = y

        if current:
            regions.append(current)

        # 合并元素过少（< 2）的区域到相邻区域
        merged: list[list[Any]] = []
        for reg in regions:
            if len(reg) < 2 and merged:
                # 贴到前一区域
                merged[-1].extend(reg)
            else:
                merged.append(reg)

        return merged
    except Exception as e:
        _log.warning("semantic_dom_tree._cluster_regions 异常 %s", e)
        # 异常时把所有元素视为一个区域
        return [list(elements)] if elements else []


def _region_name_and_summary(
    elements: list[Any], keywords: list[str]
) -> tuple[str, str]:
    """根据区域内元素类型 + 关键词，生成一个中文区域名与摘要。"""
    try:
        type_counter: Counter = Counter(_classify(el) for el in elements)
        total = len(elements) or 1

        input_ratio = type_counter.get(_TYPE_INPUT, 0) / total
        button_ratio = type_counter.get(_TYPE_BUTTON, 0) / total
        link_ratio = type_counter.get(_TYPE_LINK, 0) / total
        table_ratio = type_counter.get(_TYPE_TABLE, 0) / total

        texts = [_get_text(el) for el in elements]
        all_text = " ".join(texts).lower()

        # 命名判定（按优先级）
        name = "其他内容区"
        if (
            table_ratio >= 0.25
            or "表格" in all_text
            or "列表" in all_text
            or "grid" in all_text
        ):
            name = "数据列表区"
        elif input_ratio >= 0.3 or any(
            k in all_text
            for k in (
                "查询",
                "搜索",
                "search",
                "密码",
                "账号",
                "邮箱",
                "username",
                "password",
            )
        ):
            # 进一步区分查询表单 vs 普通表单
            if any(k in all_text for k in ("查询", "搜索", "search", "筛选", "filter")):
                name = "查询表单区"
            else:
                name = "数据填写区"
        elif button_ratio >= 0.25:
            # 操作区：进一步区分
            if any(
                k in all_text
                for k in ("新增", "创建", "添加", "新建", "create", "add", "new")
            ):
                name = "记录管理区"
            else:
                name = "操作按钮区"
        elif link_ratio >= 0.25:
            name = "导航区"
        elif button_ratio >= 0.15 and input_ratio >= 0.15:
            name = "混合交互区"

        # 摘要
        type_bits = []
        if type_counter.get(_TYPE_INPUT, 0):
            type_bits.append(f"{type_counter[_TYPE_INPUT]} 个输入项")
        if type_counter.get(_TYPE_BUTTON, 0):
            type_bits.append(f"{type_counter[_TYPE_BUTTON]} 个按钮")
        if type_counter.get(_TYPE_LINK, 0):
            type_bits.append(f"{type_counter[_TYPE_LINK]} 个链接")
        if type_counter.get(_TYPE_TABLE, 0):
            type_bits.append(f"{type_counter[_TYPE_TABLE]} 个表格元素")
        type_desc = "、".join(type_bits) if type_bits else f"{total} 个元素"

        kw_display = "、".join(keywords[:4]) if keywords else "无显著关键词"
        summary = f"{total} 个元素，{type_desc}，关键词：{kw_display}"

        return name, summary
    except Exception as e:
        _log.warning("semantic_dom_tree._region_name_and_summary 异常 %s", e)
        return "其他内容区", f"{len(elements)} 个元素（解析异常）"


def _compact_element(el: Any, max_text_len: int = 120) -> dict[str, Any]:
    """把单个元素压缩成 AI 友好的小 dict（保留 tag/text/type/关键字段）。"""
    try:
        from ._classify import _get_tag

        tag = _get_tag(el) or "element"
        text = _get_text(el)
        if len(text) > max_text_len:
            text = text[:max_text_len] + "…"
        return {
            "tag": tag,
            "type": _classify(el),
            "text": text,
            "y": round(_get_y(el), 1),
        }
    except Exception as e:
        _log.warning("semantic_dom_tree._compact_element 异常 %s", e)
        return {"tag": "element", "type": _TYPE_OTHER, "text": "(解析异常)", "y": 0}
