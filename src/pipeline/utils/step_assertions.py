# -*- coding: utf-8 -*-
"""「每个操作步骤都必须携带断言」—— 步骤级预期的**单一事实源**。

契约（为什么必须有本模块）:
  用例的「操作步骤」与「断言」此前是**两套并行表达**：步骤是动作序列，断言只挂在
  用例级 `checks`。评审页（intent-review）虽渲染"操作步骤（含逐步预期）"，但当步骤
  自身没有 expected 时只能把整体预期挂在最后一步（`md-step-expect-none`）——即
  "只在末尾统一写整体预期"，违反工程纪律，也让执行端无法逐步判定。

  本模块把"某类动作完成后**可观察的预期**"一次性收口：任何生成/定稿路径都只能
  import 取用，不得再各写一套。判据只依赖动作类型与控件通用语义，零业务/模块硬编码。

输出契约（每个步骤对象新增两个键）:
  expected: str  —— 该步骤的断言（人类可读，评审页"预期"位渲染）
  assert:   str | None —— 确定性判定标记（执行端对可机械判定的步骤只读该标记，
                          无标记的步骤由语义/结构化 checks 承担，不凭空判 PASS）
"""
from __future__ import annotations

# 动作 → (预期文案模板, 确定性判定标记)。标注 [in] 的模板会带入 step.value。
_NAV_TYPES = ("navigate", "goto")
_INPUT_TYPES = ("fill", "type_text")
_STATE_TYPES = {"select_option": "选项已选中", "check": "控件已勾选",
                "uncheck": "控件已取消勾选", "clear": "字段已清空"}
_EVIDENCE_TYPES = ("screenshot",)  # 仅取证，不是操作步骤（不计入步骤断言约束）

# 结构标记（执行端只读）：表示该步骤有可机械判定的观察点
ASSERT_NAVIGATE = "page_loaded"
ASSERT_INPUT = "field_value_set"
ASSERT_STATE = "control_state_set"
ASSERT_ACTION = "action_ok"


def derive_step_expectation(step: dict) -> dict:
    """为单个步骤派生 (expected, assert) —— 全引擎唯一实现。

    :param step: 步骤对象（`type`/`action` + `target_hint`/`target`/`value`）
    :return: ``{"expected": str, "assert": str | None}``；`expected` 恒非空。
    """
    if not isinstance(step, dict):
        return {"expected": "步骤执行成功且页面无致命错误", "assert": ASSERT_ACTION}
    _t = str(step.get("type") or step.get("action") or "").strip().lower()
    _target = str(
        step.get("target_hint") or step.get("target") or step.get("selector") or ""
    ).strip()
    _val = step.get("value")
    _vs = "" if _val is None else str(_val).strip()
    _where = "目标控件" if not _target else "「%s」" % _target[:40]

    if _t in _NAV_TYPES:
        return {
            "expected": "导航到达目标页且主内容区可见（无 404/500）",
            "assert": ASSERT_NAVIGATE,
        }
    if _t in _INPUT_TYPES:
        _tail = "，值等于 %r" % _vs if _vs else "且值非空"
        return {
            "expected": "字段接受输入%s" % _tail,
            "assert": ASSERT_INPUT,
        }
    if _t in _STATE_TYPES:
        _tail = "=%r" % _vs if _vs else ""
        return {
            "expected": "%s%s（控件状态更新成功）" % (_STATE_TYPES[_t], _tail),
            "assert": ASSERT_STATE,
        }
    if _t == "press_key":
        return {"expected": "按键生效（如回车触发提交/搜索），页面无致命错误",
                "assert": ASSERT_ACTION}
    if _t in ("hover", "double_click", "right_click", "click"):
        return {
            "expected": "点击%s生效（元素可点击，页面无致命错误）" % _where
            if _t == "click" else "交互%s生效，页面无致命错误" % _where,
            "assert": ASSERT_ACTION,
        }
    if _t in ("upload_file", "set_date", "paste", "scroll"):
        return {"expected": "操作%s生效，控件值/页面状态符合预期" % _where,
                "assert": ASSERT_ACTION}
    if _t in ("wait", "wait_for"):
        return {"expected": "等待结束，页面状态稳定（可继续操作）", "assert": None}
    if _t == "verify":
        _own = str(step.get("expect") or step.get("expected") or "").strip()
        return {"expected": _own or "断言成立", "assert": None}
    return {"expected": "步骤%s执行成功且页面无致命错误" % _where, "assert": ASSERT_ACTION}


def ensure_step_assertions(case: dict, *, step_key: str = "test_steps") -> int:
    """就地补齐用例每个操作步骤的断言（返回补齐/规整的步骤数）。

    - 取 `case[step_key]`（缺失回落 `case["actions"]`）；
    - 纯取证步骤（screenshot）不是操作步骤，从步骤表移除（保留在 actions 供执行取证）；
    - 每个剩余步骤：缺 `expected` 则写入派生断言；缺 `assert` 则写入确定性标记。
    """
    if not isinstance(case, dict):
        return 0
    steps = case.get(step_key)
    if not isinstance(steps, list) or not steps:
        steps = case.get("actions")
    if not isinstance(steps, list) or not steps:
        return 0
    _out: list = []
    _n = 0
    for s in steps:
        if not isinstance(s, dict):
            continue
        _t = str(s.get("type") or s.get("action") or "").strip().lower()
        if _t in _EVIDENCE_TYPES:
            continue  # 取证步骤不计入操作步骤
        _d = derive_step_expectation(s)
        if not str(s.get("expected") or "").strip():
            s["expected"] = _d["expected"]
            _n += 1
        if _d["assert"] and not str(s.get("assert") or "").strip():
            s["assert"] = _d["assert"]
            _n += 1
        _out.append(s)
    if len(_out) != len(steps):
        case[step_key] = _out
    if _out and not isinstance(case.get("actions"), list):
        case["actions"] = list(_out)
    return _n
