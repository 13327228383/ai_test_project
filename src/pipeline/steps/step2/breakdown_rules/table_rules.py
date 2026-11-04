# -*- coding: utf-8 -*-
"""表格规则：从结构化表格（table_meta）提取原子需求三元组。

与 breakdown_rules.py 的文本规则互补：
  - 文本规则：匹配 Markdown 文本中的标准结构（操作步骤→预期行为等）
  - 表格规则：匹配 table_extractor 提取的结构化表格（headers + rows）

表格规则在 run_all_pipeline 中优先于文本规则执行。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)

TableRuleFunc = Callable[[dict[str, Any], str], list[dict[str, Any]]]

# ─ 表格规则注册表 ──
_TABLE_RULE_REGISTRY: list[TableRuleFunc] = []


def register_table_rule(func: TableRuleFunc) -> TableRuleFunc:
    """装饰器：注册一个表格规则提取函数。"""
    _TABLE_RULE_REGISTRY.append(func)
    return func


def get_table_rules() -> list[TableRuleFunc]:
    """获取所有已注册的表格规则。"""
    return list(_TABLE_RULE_REGISTRY)


# ══════════════════════════════════════════════════════════════
# 表单属性表规则
# ══════════════════════════════════════════════════════════════

# 通用列名关键词（中英文，用于识别表单属性表）
# 禁止硬编码业务术语；仅匹配"名称/字段/必填/类型/备注"等通用元数据列
_FORM_ATTR_NAME_KEYS = {"名称", "字段", "字段名", "属性", "name", "field", "column"}
_FORM_ATTR_REQUIRED_KEYS = {"是否必填", "必填", "required", "必填项", "是否必须"}
_FORM_ATTR_TYPE_KEYS = {"属性类型", "类型", "数据类型", "字段类型", "type", "控件类型"}
_FORM_ATTR_NOTE_KEYS = {
    "备注",
    "说明",
    "描述",
    "note",
    "remark",
    "constraint",
    "约束",
    "限制",
}


def _find_column_index(headers: list[str], keywords: set[str]) -> int:
    """在表头中查找匹配关键词的列索引，未找到返回 -1。"""
    for idx, header in enumerate(headers):
        h = header.strip()
        if h in keywords:
            return idx
        # 宽松匹配：关键词是表头的子串（如"是否必填"包含"必填"）
        for kw in keywords:
            if kw in h:
                return idx
    return -1


def _build_field_source_quote(row: list[str]) -> str:
    """从表格行构建 source_quote，格式：字段名 | 类型 | 备注。"""
    parts = [v.strip() for v in row if v.strip()]
    return " | ".join(parts)[:120]


def _is_required_value(cell: str) -> bool:
    """判断单元格值是否表示'必填'。"""
    v = cell.strip().lower()
    return v in {"是", "yes", "true", "1", "必填", "必须", "y"}


@register_table_rule
def _rule_form_attribute_table(
    table_meta: dict[str, Any], chunk_id: str
) -> list[dict[str, Any]]:
    """★ v25 根因2 修复(2026-07-21): 字段定义表不生成需求，返回空列表。

    原bug链路（4层根因）:
      L1: 生成 "辅材编号字段"、"按钮字段" 等伪需求（operation 不是真实操作）
      L2: 这些伪需求进入 Step3 生成无意义用例（找"辅材编号字段"按钮必然失败）
      L3: 字段定义表是"怎么做"（实现细节），不是"做什么"（需求）
      L4: table_rules 主动把字段定义当需求，是 Step2 拆解质量差的根因之一

    根因: 字段定义表是控件实现细节，不是用户操作需求。
          "辅材编号 | 辅入框 | 限50位" 描述的是"辅材编号字段长什么样"，
          不是"用户能做什么操作"。需求是"用户能新增辅材"（做什么）。

    修复: 完全禁用字段定义表的需求提取，返回空列表。
          字段定义表中的真实操作（如"点击确定"按钮）由 AI 兜底提取，
          AI 有语义理解能力，能区分"字段定义"和"操作步骤"。

    通用性: 适用于所有系统的字段定义表（任何行业的字段定义表都是实现细节）。
    覆盖性: 影响 _rule_form_attribute_table 规则覆盖的所有表格。
    合规: AGENTS.md §4.1 "一个需求→一个完整测试用例" + §10.8 "禁止治标不治本"。

    自我反驳三次:
      反驳1: "删了会不会丢失字段信息？" → 不会，字段信息在需求文档原文中，
             AI 兜底提取真实操作时能读到原文，能正确识别"这是字段定义不是操作"。
      反驳2: "Step3 需要字段元数据生成 fill 动作？" → Step3 当前没有字段元数据消费，
             _infer_fill_target 已从 DOM placeholder 和 RAG API 推断，不依赖字段定义表。
      反驳3: "通用性如何保证？" → 字段定义表的"字段名|类型|说明"格式跨系统通用，
             都是实现细节不是需求，所有系统都适用此修复。
    """
    # ★ v25 根因2: 字段定义表不当需求，直接返回空
    _log.debug(
        "[table_rules] ★v25根因2★ 字段定义表不当需求，跳过提取 chunk=%s", chunk_id
    )
    return []
