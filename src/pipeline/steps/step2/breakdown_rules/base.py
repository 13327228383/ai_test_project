# -*- coding: utf-8 -*-
"""需求拆解规则基类。

Step2 规则与 Step3 不同：Step2 是流水线（预处理→提取→校验→清洗→标签），
不是独立并行规则。因此基类按阶段（stage）分组。
"""

import re
from typing import Any


class BaseBreakdownRule:
    """需求拆解规则基类。

    子类需覆盖：
      - rule_id / name / description / stage / priority（类属性）
      - 对应阶段的处理函数（extract / check / clean / …）
    """

    # ── 元数据（子类必须覆盖）──
    rule_id: str = ""  # 唯一标识，如 "standard_structure"
    name: str = ""  # 中文名，如 "标准结构提取"
    description: str = ""  # 描述
    stage: str = ""  # 阶段: "preprocess" / "extract" / "check" / "clean" / "label"
    priority: int = 10  # 优先级（越大越先执行，默认 10）
    version: str = "1.0"

    # ── 元数据 ──
    def meta(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "stage": self.stage,
            "priority": self.priority,
            "version": self.version,
        }

    # ── 工具方法 ──
    @staticmethod
    def _strip_bullet(text: str) -> str:
        """去掉编号/项目符号前缀。"""
        return re.sub(r"^\d+\.\s*|^[-*]\s*", "", text).strip()


# ══════════════════════════════════════════════════
# 阶段 1: 预处理规则（preprocess）
# ══════════════════════════════════════════════════


class PreprocessRule(BaseBreakdownRule):
    """预处理规则基类：输入文本 → 输出清洗后文本。"""

    stage = "preprocess"

    def apply(self, text: str) -> str:
        raise NotImplementedError


# ══════════════════════════════════════════════════
# 阶段 2: 提取规则（extract）
# ══════════════════════════════════════════════════


class ExtractRule(BaseBreakdownRule):
    """提取规则基类：输入切片文本 → 输出三元组列表。"""

    stage = "extract"

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError


# ══════════════════════════════════════════════════
# 阶段 3: 校验规则（check）
# ══════════════════════════════════════════════════


class CheckRule(BaseBreakdownRule):
    """校验规则基类：输入需求 → 输出校验问题列表。"""

    stage = "check"

    def apply(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


# ══════════════════════════════════════════════════
# 阶段 4: 清洗规则（clean）
# ══════════════════════════════════════════════════


class CleanRule(BaseBreakdownRule):
    """清洗规则基类：输入三元组列表 → 输出去重/清洗后的三元组列表。"""

    stage = "clean"

    def apply(self, triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError


# ══════════════════════════════════════════════════
# 阶段 5: 标签规则（label）
# ══════════════════════════════════════════════════


class LabelRule(BaseBreakdownRule):
    """标签规则基类：输入三元组列表 → 输出带标签的三元组列表。"""

    stage = "label"

    def apply(
        self, triples: list[dict[str, Any]], context: dict[str, Any] = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
