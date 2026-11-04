# -*- coding: utf-8 -*-
"""执行顺序异常反向覆盖生成器：跳步 / 乱序 / 并发 / 中途返回。

覆盖维度
--------
- step_skip       : 跳过中间步骤（必填项 / 前置依赖步骤）直接执行后续
- step_reverse    : 倒序执行步骤（验证步骤间依赖是否被强校验）
- mid_back        : 执行到中间时浏览器后退，再次提交（验证状态机）
- concurrent      : 多 tab/窗口并发触发同一操作（验证锁/事务）

★ v0.1 骨架 ★
仅产出最小占位（step_skip：跳过第一个 fill 步骤）。其它子类型待真实意图样本积累后补充。
"""

from typing import Any
import copy

from ._base import InverseGenContext, build_inverse_intent


def expand(positive: dict[str, Any], ctx: InverseGenContext) -> list[dict[str, Any]]:
    """对单个正向意图扩展出执行顺序异常维度的反向意图。

    返回:
        反向意图列表（v0.1 仅返回 1 条：跳过第一个 fill 步骤）
    """
    inverse: list[dict[str, Any]] = []

    # 仅对 step 数 ≥ 3 的意图做跳步覆盖（太短的意图跳了就没业务语义）
    src_steps = positive.get("steps") or []
    if isinstance(src_steps, list) and len(src_steps) >= 3:
        skipped = _build_step_skip(positive, ctx)
        if skipped:
            inverse.append(skipped)

    # TODO（v0.2）：step_reverse —— 倒序执行需要分析步骤间真实依赖
    # TODO（v0.2）：mid_back —— 需要在 step4 增加"浏览器后退"动作支持
    # TODO（v0.2）：concurrent —— 需要 step4 多 tab 并发能力

    return inverse


def _build_step_skip(
    positive: dict[str, Any], ctx: InverseGenContext
) -> dict[str, Any]:
    """跳过第一个 fill（必填）步骤，直接执行后续，验证表单校验是否兜底。"""
    src_steps = positive.get("steps") or []
    skipped_steps: list[dict[str, Any]] = []

    skipped_one = False
    for step in src_steps:
        if not isinstance(step, dict):
            continue
        if not skipped_one and step.get("action") == "fill":
            # 跳过第一个 fill 步骤
            skipped_one = True
            continue
        skipped_steps.append(copy.deepcopy(step))

    if not skipped_one:
        # 没有 fill 步骤，没法做"必填空跳"覆盖
        return None

    # 追加防御断言
    skipped_steps.append(
        {
            "action": "verify",
            "description": "验证表单在必填项缺失时是否拦截提交",
            "expected": "出现校验错误提示 / 提交按钮被禁用 / 后端返回 4xx",
            "_inverse_assertion": "required_field_validation",
        }
    )

    return build_inverse_intent(
        source_intent=positive,
        dimension="ordering",
        sub_kind="step_skip",
        title_suffix="[跳过必填步骤]",
        description=(
            f"基于正向意图 {positive.get('case_id')} 进行跳步测试："
            f"省略第一个 fill 操作直接走后续步骤，验证前端校验/后端兜底是否生效。"
        ),
        steps=skipped_steps,
        risk_points=[
            "前端校验缺失，依赖后端兜底",
            "后端校验缺失，可写入空必填字段",
            "状态机允许从中间状态直接到终态",
        ],
        expected_defense="表单提交被拦截，必填项报错；或后端返回参数校验错误",
        ctx=ctx,
    )


__all__ = ["expand"]
