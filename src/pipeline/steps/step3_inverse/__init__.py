# -*- coding: utf-8 -*-
"""
反向覆盖生成器（Inverse Coverage Generator）—— v0.1 骨架
========================================================

职责
----
对 step3 产出的"正向意图"做**反向全面覆盖**——在已有正向路径之外，
针对每个意图自动生成**异常路径 / 攻击路径 / 边界路径**的结构化测试意图，
扔回 step4 主流程执行。

不要把这个模块和 step4/browser_core/single.py 的"AI 兜底循环"混淆——
那个是当 step_action 无法结构化时的运行时救场，本模块是**生成阶段**的
反向意图扩展，产出物仍然是结构化意图，走 step4 主流程的
_execute_structured_actions 路径。

子模块（按反向覆盖维度划分）
----------------------------
- _base.py     — 共享数据类与上下文（InverseGenContext / InverseIntent 工厂）
- replay.py    — 重放 / 幂等性 / 防重提交覆盖
- security.py  — 越权 / 注入 / CSRF / XSS / 敏感数据泄漏覆盖
- ordering.py  — 执行顺序异常：跳步 / 乱序 / 并发 / 中途返回 / 必填空跳过

顶层 API
--------
generate_inverse_intents(positive_intents) -> list[dict]
    输入：step3 产出的正向意图列表（v9 5 字段格式：title/title/type/risk_level/required_tracks）
    输出：反向意图列表（结构与正向意图一致，可直接被 step4 消费）

★ v0.1 骨架阶段说明 ★
---------------------
当前每个生成器只产出**最小可运行占位**——足以让 step4 把它当作正常意图执行，
但具体反向规则还没填充。等正向流水线跑通、积累真实意图样本后再补充：
- 哪些意图适合做哪种反向覆盖（不是所有意图都需要重放测试）
- 反向 step 的具体生成规则（要不要调 AI、调几次）
- 如何判定反向测试"通过"（防御生效=通过 vs 攻击成功=失败）
"""

from typing import Any

from ._base import InverseGenContext, build_inverse_intent
from . import replay
from . import security
from .security import SecurityInverseExpander
from . import ordering


def generate_inverse_intents(
    positive_intents: list[dict[str, Any]],
    enabled_dimensions: list[str] = None,
) -> list[dict[str, Any]]:
    """对正向意图列表做反向覆盖扩展。

    参数:
        positive_intents:  step3 输出的正向意图列表
        enabled_dimensions: 启用的反向维度，None=全部启用
                            可选值: "replay" / "security" / "ordering"
    返回:
        反向意图列表，每个元素结构与 positive_intents 一致，可直接喂 step4

    ★ v0.1 行为 ★
    - 仅生成最小占位意图，不调 AI、不分析具体业务
    - 真实业务规则待正向流程跑通后补全
    """
    if not positive_intents:
        return []
    if enabled_dimensions is None:
        enabled_dimensions = ["replay", "security", "ordering"]

    inverse: list[dict[str, Any]] = []
    ctx = InverseGenContext()

    for positive in positive_intents:
        if not isinstance(positive, dict):
            continue
        if positive.get("_generation_failed"):
            # 跳过 AI 生成失败的占位意图，反向覆盖没意义
            continue

        if "replay" in enabled_dimensions:
            inverse.extend(replay.expand(positive, ctx))
        if "security" in enabled_dimensions:
            inverse.extend(security.expand(positive, ctx))
        if "ordering" in enabled_dimensions:
            inverse.extend(ordering.expand(positive, ctx))

    return inverse


__all__ = [
    "generate_inverse_intents",
    "InverseGenContext",
    "build_inverse_intent",
    "replay",
    "security",
    "ordering",
    "SecurityInverseExpander",
]
