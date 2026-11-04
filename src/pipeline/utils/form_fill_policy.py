# -*- coding: utf-8 -*-
"""「提交前是否补全表单必填项」策略 —— 单一事实源（生成端判定、执行端只读）。

why: 该策略此前只存在于执行层，且两个调用点的**输入**各自拼装：结构化路径读
  `_explore_task`（多数结构化用例没有这个字段）→ 传空串 → 极性判据退化为"补全"，
  于是「空提交/非法值触发校验」类用例的表单被自动填满（价格/库存拿到合法样例值），
  提交成功 → 被测的错误反馈面结构性不存在；实测 11 条此类用例中 1 条落无结论、
  10 条只在靶子「名称已存在」这一无关规则上通过（判定证据与被测需求不符）。
  正解：语义在 Step3 落库前一次性判定并写结构化字段 `form_fill_policy`，
  执行端只读不推；字段缺失的历史行回退到同一判据函数（不另立第二套词表）。
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# 结构化字段取值：提交前补全 / 保持空（不补全）
FORM_FILL_REQUIRED = "fill_required"
FORM_FILL_LEAVE_EMPTY = "leave_empty"

# 「服务端校验」型需求的极性例外：客户端校验必须先通过，请求才能到达服务端。
# 只认「服务端 + 邻近的校验结果词」，不认裸"服务端"——否则"不允许请求到达服务端"
# （客户端拦截型）会被误翻转。
_SERVER_REJECT_RE = re.compile(
    r"(服务端|后端|服务器端)[^。；;]{0,12}?(校验)?(失败|拒绝|不通过|异常|发现|报错)"
)
_CLIENT_BLOCK_WORDS: tuple = (
    "客户端拦截", "客户端阻止", "客户端校验失败", "客户端校验拦截", "前端拦截",
    "前端校验失败", "浏览器拦截", "阻止表单提交", "拦截表单", "不允许请求",
    "阻止提交", "不提交", "未勾选", "未选择", "未填写", "未输入", "留空", "为空",
)


def should_autofill(operation: str, expected: str, description: str) -> bool:
    """本用例提交前是否应补全表单其余字段（极性门控，唯一判据）。

    期望本身即「错误/拒绝」提示 → 不补全（补全会让表单顺利提交，掩盖被测校验路径）。
    例外：「服务端校验失败」型需求的前提是客户端校验先通过（载荷完整），此时须补全，
    否则请求根本到不了服务端，被测路径不可达。
    """
    from src.pipeline.step3._rule_extractor_keywords import expects_error_feedback

    _texts = [str(operation or ""), str(expected or ""), str(description or "")]
    if not expects_error_feedback(*_texts):
        return True
    _blob = " ".join([str(description or ""), str(expected or "")])
    if _SERVER_REJECT_RE.search(_blob) and not any(
        _w in _blob for _w in _CLIENT_BLOCK_WORDS
    ):
        _log.info(
            "[FormComplete] 需求为「服务端校验」型（客户端须先通过）→ 补全必填项"
        )
        return True
    _log.info("[FormComplete] 需求期望为错误/拒绝反馈 → 跳过必填项补全")
    return False


def _case_texts(case: dict) -> tuple[str, str, str]:
    """用例自己声明的语义文本（operation / expected / description）。

    有 `_explore_task`（Agent 引导执行用例）时用它提炼过的三字段——与执行端 Agent
    路径同一来源；否则用用例 payload 的 `description` + `expected_result`
    （结构化执行用例的唯一权威期望文本）。
    """
    _task = case.get("_explore_task") if isinstance(case, dict) else {}
    _task = _task if isinstance(_task, dict) else {}
    _op = str(_task.get("operation") or "")
    _expected = str(_task.get("expected_feedback") or case.get("expected_result") or "")
    _desc = str(_task.get("description") or case.get("description") or "")
    return _op, _expected, _desc


def declare_form_fill_policy(case: dict) -> str:
    """生成端唯一生产点：判定并写入结构化字段 `form_fill_policy`，返回该值。"""
    if not isinstance(case, dict):
        return ""
    _op, _expected, _desc = _case_texts(case)
    _policy = (
        FORM_FILL_REQUIRED if should_autofill(_op, _expected, _desc)
        else FORM_FILL_LEAVE_EMPTY
    )
    case["form_fill_policy"] = _policy
    return _policy


def autofill_allowed(case: dict) -> bool:
    """执行端只读入口：读 `form_fill_policy` 决定是否补全。

    why: 判据只在生成端跑一次，执行端不得用自己的（可能为空的）输入重推；
      字段缺失（历史用例行）时回退到 `should_autofill` 对同一用例文本求值。
    """
    if not isinstance(case, dict):
        return True
    _policy = str(case.get("form_fill_policy") or "").strip()
    if _policy:
        return _policy == FORM_FILL_REQUIRED
    _op, _expected, _desc = _case_texts(case)
    return should_autofill(_op, _expected, _desc)


__all__ = [
    "FORM_FILL_REQUIRED",
    "FORM_FILL_LEAVE_EMPTY",
    "should_autofill",
    "declare_form_fill_policy",
    "autofill_allowed",
]
