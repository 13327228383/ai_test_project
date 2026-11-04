# -*- coding: utf-8 -*-
"""
需求文档门禁 (RequirementGate) — Facade
======================================
采用 mixin 多继承模式，实际逻辑分布在 requirement_gate_mixins/ 子目录中。

★ P1: 两个核心门禁：
  1. 需求拆解后门禁：校验拆解结果是否在合并需求文档中有依据
  2. 测试用例生成后门禁：校验测试用例是否在合并需求文档中有依据

校验方式：
  - 拿着AI生成的内容（拆解结果/测试用例）
  - 去查合并的需求文档（Confluence + 上传文件）
  - 有相关内容 → PASS
  - 没有相关内容 → BLOCK（阻断，不是仅警告）
"""


from .base import _GateBaseMixin
from .check import _GateCheckMixin
from .constants import GateResult  # noqa: F401  re-export for external callers


class RequirementGate(_GateBaseMixin, _GateCheckMixin):
    """
    需求文档门禁。

    核心逻辑：
    1. 加载合并的需求文档（Confluence + 上传文件）作为基准
    2. 拿着AI生成的内容去匹配基准
    3. 匹配不上 → 阻断（blocker）
    """

    pass
