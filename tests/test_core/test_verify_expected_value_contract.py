# -*- coding: utf-8 -*-
"""D37 契约：verify 步骤的**结构化期望值**（value）优先于自然语言 expect。

根因（实测 TC-REQ-040-E01 假 ERROR）：
    verify 步骤同时带 `value="用户名或密码错误"`（生成侧落库的精确期望串）
    与 `expect="错误凭证被拒绝，提示错误信息/登录失败"`（需求整句）。
    执行端只拿 `expect` 切 2 字子词去页面匹配 → 页面是"用户名或密码错误" →
    16 个切出来的子词一个都没命中 → 判 FAIL，而靶子行为完全正确。

判据（单一事实源）：`_guided_verify` 的 `_check_expects` 首位必须是
用例声明的结构化期望值 `expected_value`；`expect` 退为语义兜底。
"""
from __future__ import annotations

import inspect
import re

from src.agent.explorer_mixins import _action


def _src() -> str:
    return inspect.getsource(_action._execute_guided) if hasattr(
        _action, "_execute_guided"
    ) else ""


def test_guided_verify_accepts_expected_value():
    """_guided_verify 必须暴露 expected_value 形参（结构化期望值入口）。"""
    sig = inspect.signature(_action.ActionMixin._guided_verify)
    assert "expected_value" in sig.parameters, (
        "_guided_verify 缺少 expected_value 形参 —— verify 无法接收用例声明的"
        "结构化期望值，只能拿需求整句做子词匹配（假 ERROR 根因）"
    )
    p = sig.parameters["expected_value"]
    assert p.default == "", "expected_value 默认值须为空串（未声明时不改变原行为）"


def test_call_site_passes_value():
    """唯一调用点必须把步骤的 value 传进去。"""
    src = inspect.getsource(_action.ActionMixin._guided_verify.__func__ if
                            hasattr(_action.ActionMixin._guided_verify, "__func__")
                            else _action.ActionMixin._guided_verify)
    # 调用点在 _execute_guided 内，检查整个模块源码
    mod = inspect.getsource(_action)
    m = re.search(r"await self\._guided_verify\(\s*page\s*,\s*_expect\s*,\s*timeout_ms\s*"
                  r"(?:,\s*expected_value\s*=\s*_value)?\s*\)", mod)
    assert m, "未找到 _guided_verify 调用点"
    assert "expected_value=_value" in m.group(0), (
        "调用点未传 expected_value=_value —— 结构化期望值丢失，修复失效"
    )


def test_expected_value_inserted_first_in_check_expects():
    """结构化期望值必须插在 _check_expects **首位**（判定优先级）。"""
    mod = inspect.getsource(_action)
    assert "_check_expects.insert(0, _ev)" in mod, (
        "结构化期望值未插入 _check_expects 首位 —— 判定优先级错误"
    )
    # insert 必须发生在 _check_expects = [expect] 之后
    i_init = mod.find("_check_expects = [expect]")
    i_ins = mod.find("_check_expects.insert(0, _ev)")
    assert i_init != -1 and i_ins != -1 and i_ins > i_init, (
        "插入顺序错误：应先初始化 _check_expects 再 insert"
    )


def test_no_new_word_table_introduced():
    """反补丁三禁：本次修复不得新增词表常量（只做判定优先级调整）。"""
    mod = inspect.getsource(_action)
    seg = mod[mod.find("_ev = str(expected_value"):mod.find("_check_expects.insert(0, _ev)") + 60]
    # 该片段内不得出现 tuple/set/list 字面量形式的词表
    assert not re.search(r"^\s*[A-Z_]{3,}\s*[:=]\s*[\(\{\[]", seg, re.M), (
        "修复片段内引入新词表 —— 违反反补丁三禁（禁新增词表）"
    )
