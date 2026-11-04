# -*- coding: utf-8 -*-
"""重放 / 幂等性 / 防重提交 反向覆盖生成器。

覆盖维度
--------
- replay_x_n      : 同一意图重复执行 N 次，观察是否限频/去重/幂等
- form_double_submit : 表单连点提交按钮（前端防抖 + 后端去重双重防御）
- back_then_resubmit : 提交后浏览器后退再次提交（绕过前端 disable）

★ v0.1 骨架 ★
仅产出最小占位（重放×3）。其它子类型待真实正向意图样本积累后补充。
"""

from typing import Any
import copy

from ._base import InverseGenContext, build_inverse_intent


def expand(positive: dict[str, Any], ctx: InverseGenContext) -> list[dict[str, Any]]:
    """对单个正向意图扩展出重放维度的反向意图。

    返回:
        反向意图列表（v0.1 仅返回 1 条：重放×3）
    """
    inverse: list[dict[str, Any]] = []

    # 仅对包含"提交/创建/支付/下单"等修改类操作的意图做重放覆盖
    # ★ v0.1 暂用粗匹配，后续应改为基于 intent_schema.event 的语义判定
    if not _is_mutation_intent(positive):
        return inverse

    inverse.append(_build_replay_x_n(positive, ctx, n=3))

    return inverse


def _is_mutation_intent(positive: dict[str, Any]) -> bool:
    """粗判一个意图是否涉及数据变更（修改类操作才有重放风险）。

    ★ v9: 基于 title 关键字判定（替代旧 intent_schema.event）。
    """
    what = str(positive.get("title") or positive.get("title") or "")
    # ★ 通用化: 移除电商特定动词(支付/下单/购买)，保留通用数据变更动词
    keywords = (
        "提交",
        "创建",
        "新增",
        "添加",
        "保存",
        "删除",
        "更新",
        "修改",
        "重置",
        "导入",
        "上传",
    )
    return any(kw in what for kw in keywords)


def _build_replay_x_n(
    positive: dict[str, Any], ctx: InverseGenContext, n: int = 3
) -> dict[str, Any]:
    """重放 N 次：把正向意图的 steps 复制 N 份串联，末尾追加防御断言。"""
    src_steps = positive.get("steps") or []
    if not isinstance(src_steps, list):
        src_steps = []

    # 复制 steps × n
    replayed_steps: list[dict[str, Any]] = []
    for round_idx in range(n):
        for step in src_steps:
            if not isinstance(step, dict):
                continue
            # 不重复跑导航（重放第 2 次起跳过 navigate，模拟用户停留页面连点）
            if round_idx > 0 and step.get("action") == "navigate":
                continue
            replayed_steps.append(copy.deepcopy(step))

    # 追加防御断言（verify 类，走 step4 主流程）
    replayed_steps.append(
        {
            "action": "verify",
            "description": f"验证连续 {n} 次执行后系统是否触发了防重放/限频/幂等保护",
            "expected": "出现限频提示 / 重复操作提示 / 仅生效一次 等防御行为",
            "_inverse_assertion": "anti_replay",
        }
    )

    return build_inverse_intent(
        source_intent=positive,
        dimension="replay",
        sub_kind="replay_x_n",
        title_suffix=f"[重放×{n}]",
        description=(
            f"基于正向意图 {positive.get('case_id')} 进行重放测试："
            f"在不刷新页面的情况下连续执行 {n} 次相同操作，验证系统是否具备"
            f"幂等性 / 防重放 / 限频 等防御能力。"
        ),
        steps=replayed_steps,
        risk_points=[
            "连续相同请求未被去重，导致重复创建/重复扣款",
            "前端 disable 失效时无后端兜底",
            "无限频策略，可被刷接口",
        ],
        expected_defense="系统应在第 2 次起拒绝/合并请求，或前端禁用提交按钮",
        ctx=ctx,
    )


__all__ = ["expand"]
