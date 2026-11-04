# -*- coding: utf-8 -*-
"""编号列表提取规则：提取阶段，匹配无显式标记的编号/项目符号列表。"""

import re
import logging
from typing import Any
from .base import ExtractRule
from .predicate import has_executable_predicate

_log = logging.getLogger(__name__)

# ── 编号列表行正则（匹配完整行）──
_NUMBERED_LINE = re.compile(r"^\d+\.\s+.+", re.MULTILINE)
# ── 项目符号行正则（匹配完整行）──
_BULLET_LINE = re.compile(r"^[-*]\s+.+", re.MULTILINE)


# 出现在行尾的元数据词：说明该行是字段定义/属性描述/参数说明，不是操作步骤。
_PSEUDO_OP_SUFFIXES = (
    "字段",
    "属性",
    "参数",
    "控件",
    "类型",
    "数据类型",
    "字段类型",
    "属性类型",
    "列名",
    "字段名",
    "字段说明",
    "属性说明",
    "参数说明",
)


def _is_pseudo_operation(line: str) -> bool:
    """判断一行是否是伪操作（字段定义/属性描述/参数说明）。

    判据（满足任一即伪操作）:
      1. 行结尾是元数据词（字段/属性/参数/控件/类型）
      2. 行包含"|"分隔符且长度<50（表格行被错误识别为编号行）
      3. 行是"字段名+冒号+说明"格式（如"辅材编号: 输入框"）

    纯基于文档结构特征判定，不依赖业务术语；命中的行即便含交互动词
    （"辅材编号字段：输入框"）也不是可测行为。
    """
    if not line:
        return True
    line = line.strip()
    # 规则1: 结尾是元数据词
    for suffix in _PSEUDO_OP_SUFFIXES:
        if line.endswith(suffix):
            return True
    # 规则2: 表格行（含|分隔符且短）
    if "|" in line and len(line) < 50:
        return True
    # 规则3: "字段名: 说明"格式（冒号前是名词，冒号后是控件类型/约束）
    #   如 "辅材编号: 输入框, 限50位" / "供应商: 必填"
    if "：" in line or ":" in line:
        sep = "：" if "：" in line else ":"
        left, _, right = line.partition(sep)
        left = left.strip()
        right = right.strip()
        # 左侧是短名词（<10字），右侧含控件类型词 → 字段定义
        _control_type_words = (
            "输入框",
            "下拉框",
            "单选",
            "多选",
            "按钮",
            "文本框",
            "日期",
            "开关",
            "复选框",
            "字符串",
            "数字",
            "布尔",
            "必填",
            "非必填",
            "选填",
        )
        if len(left) < 10 and any(w in right for w in _control_type_words):
            return True
    return False


class NumberedListRule(ExtractRule):
    """从编号/项目符号列表中提取操作（无显式 **操作步骤** 标记时）。

    适用于：文档中直接列出操作步骤但未用 `**操作步骤**` 标记的情况。
    操作行须陈述可测行为（交互动作 / 系统反应 / 情态约束 / HTTP 接口）。
    """

    rule_id = "numbered_list"
    name = "编号列表提取"
    description = "从无显式标记的编号/项目符号列表中提取可测行为行"
    priority = 80

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        if not chunk_text or not chunk_text.strip():
            return []

        # 收集所有操作行
        numbered_lines = _NUMBERED_LINE.findall(chunk_text)
        bullet_lines = _BULLET_LINE.findall(chunk_text)
        all_lines = numbered_lines + bullet_lines

        if not all_lines:
            return []

        # 过滤：只保留陈述了可测行为的行（判据唯一实现在 predicate.has_executable_predicate）
        action_lines = [
            self._strip_bullet(l) for l in all_lines if has_executable_predicate(l)
        ]

        if not action_lines:
            return []

        # 含可测行为信号也可能是字段定义（"1. 辅材编号字段：输入框"），再按文档结构过滤
        action_lines = [line for line in action_lines if not _is_pseudo_operation(line)]
        if not action_lines:
            return []

        # 提取前置条件
        cond_match = re.compile(
            r"\*\*前置条件\*\*[:：]?\s*\n((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)",
            re.IGNORECASE,
        ).search(chunk_text)
        condition = ""
        if cond_match:
            cond_lines = [
                self._strip_bullet(l)
                for l in cond_match.group(1).strip().split("\n")
                if l.strip()
            ]
            condition = "; ".join(cond_lines) if cond_lines else ""

        triples = []
        for op in action_lines:
            # expected_feedback 必须是页面可观测的真实业务反馈；模板化文本（"系统执行X并返回
            # 操作结果"）在任何页面都匹配不到，会让 Step4 退化成 element_exists 假 PASS。
            triples.append(
                {
                    "operation": op,
                    "condition": condition,
                    "expected_feedback": _infer_success_hint(op),
                    "source_quote": op,
                    "source_section": chunk_id,
                    "confidence": 0.80,
                    "extraction_method": "rule",
                    "is_implicit": 0,  # 保持显式需求，因为 eb 非空且基于 operation
                }
            )

        return triples


def _infer_success_hint(operation: str) -> str:
    """基于 operation 推断真实业务成功反馈（多候选逗号分隔）。

    多候选是为了跨系统/跨行业兼容（"新增成功/添加成功/创建成功"），Step4 文本断言
    用多值匹配，任一命中即真 PASS。唯一实现在 `_rule_extractor_keywords.infer_expected_feedback`
    （叶子模块，无项目内依赖，避免循环导入），此处只委托不另立词表。
    """
    from src.pipeline.step3._rule_extractor_keywords import infer_expected_feedback

    return infer_expected_feedback(operation)
