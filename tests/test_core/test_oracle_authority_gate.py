# -*- coding: utf-8 -*-
"""判定权归位门禁（D69/D70/D71 三条纪律的静态护栏）。

背景
----
本轮实测暴露了同一形状的三处缺陷 —— **上游已有权威事实，下游却另起一套推导**：

  D69  用例声明的结构化断言（checks）是权威 oracle，但引导层把「需求整句回抄
       式」的自然语言验证点当作硬门禁；该验证点仅靠关键词子词匹配、且运行期
       零 AI 下语义断言不可用 → 弱 oracle 必然判 FAIL → 把权威 checks 挡在门外。
  D70  用例声明的动作序列（declared_actions）是「做什么」的唯一事实源，但引导
       层只把它当**定位提示**，从未 materialize 成可执行步骤 → 声明的测试激励
       （注入载荷、点击查询）根本没施加 → 断言在未受激励的页面上判定。
  D71  引擎规范把断言参数嵌套在 `check` 子对象内，但部分处理器把**外层** dict
       直接交给只读扁平字段的校验实现 → selector 丢失 → 静默判绿。

三条纪律都已在源码中落地。本门禁把它们钉死，防止后续重构静默回退。
"""
from __future__ import annotations

import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ACTION = "src/agent/explorer_mixins/_action.py"
_EXECUTOR = "src/pipeline/steps/step4/browser_core/single_mixins/_agent_executor.py"
_EXPLORER = "src/agent/explorer.py"
_DBUI = "src/pipeline/steps/step4/verification/_db_ui.py"


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ────────────────────────── D69 ──────────────────────────


def test_d69_structured_oracle_fact_is_threaded_end_to_end():
    """「用例是否声明结构化 oracle」必须同源透传，不得在下游重新推导。"""
    assert '"structured_checks"' in _read(_EXECUTOR), (
        "D69: _agent_executor 必须把 structured_checks 写入 _execution_guide"
    )
    assert 'has_structured_oracle=bool(_guide.get("structured_checks"))' in _read(
        _EXPLORER
    ), "D69: explorer 必须把该事实透传给 _execute_guided"
    action = _read(_ACTION)
    assert "has_structured_oracle: bool = False" in action, (
        "D69: _execute_guided 必须接收 has_structured_oracle 形参"
    )


def test_d69_weak_oracle_is_non_decisive_not_fail():
    """弱 oracle（无期望值 + 仅关键词子词未命中）在存在权威 oracle 时不得判 FAIL。"""
    action = _read(_ACTION)
    m = re.search(
        r"elif\s+not\s+_value\s+and\s+_vmethod\s*==\s*\"未匹配\"\s+and\s+"
        r"has_structured_oracle\s*:",
        action,
    )
    assert m, "D69: 缺失「弱 oracle → 非判定态」分支"


def test_d69_non_decisive_branch_does_not_append_failure():
    """该分支体内不得写入 _verification_failures（否则仍会判 FAIL）。"""
    action = _read(_ACTION)
    idx = action.index('_vmethod == "未匹配"')
    # 取该 elif 之后到下一个同级 else: 之前的片段
    tail = action[idx:]
    stop = tail.find("else:")
    body = tail[:stop] if stop != -1 else tail[:1500]
    assert "_verification_failures" not in body, (
        "D69: 非判定态分支不得追加失败项"
    )


# ────────────────────────── D70 ──────────────────────────


def test_d70_declared_actions_are_materialized_into_steps():
    action = _read(_ACTION)
    assert "执行用例声明的权威动作序列" in action, (
        "D70: 声明动作必须被 materialize 成可执行步骤"
    )
    assert "_declared_executed = True" in action, "D70: 必须置位 _declared_executed"


def test_d70_heuristic_block2_is_gated_by_declared_execution():
    """②块（从 operation/inputs 猜表单流）必须在声明动作已执行时整体关闭，防重复执行。"""
    action = _read(_ACTION)
    assert "if _enter_form and not _declared_executed:" in action, (
        "D70: ②块门控必须叠加 not _declared_executed"
    )


def test_d70_declaration_presence_is_the_ssot_gate():
    """「用例有没有声明」本身就是权威判据 —— 只要声明存在（哪怕只有 navigate），
    ②块的启发式合成就不得开工。执行端凭空加戏会施加用例没要求的业务操作，
    既污染靶子数据又把用例声明的激励冲掉（实测 TC-REQ-013-I05 假 FAIL）。"""
    action = _read(_ACTION)
    assert "_declared_actions_present" in action, (
        "D70: 必须用「声明是否存在」作为唯一门控"
    )
    assert action.count("if _declared_actions_present:") == 1, (
        "D70: 声明门控只准一处"
    )
    # 门控不得退回「只看非导航步骤 / 只看 _enter_form」这类代理判据
    assert "if _declared_steps and not _enter_form:" not in action, (
        "D70: 已废弃的代理门控残留（应以声明是否存在为准）"
    )


def test_d76_action_synonym_family_is_reused_not_redefined():
    """「提交/保存/确定/确认 是同一动作」只能有一处表达；族ABB不得另起词表。"""
    action = _read(_ACTION)
    assert "_FORM_ENTRY_WORDS" in action and "_SUBMIT_WORDS" in action, (
        "D76: 动作语义族必须复用模块级唯一词表"
    )
    assert action.count("_SUBMIT_WORDS: tuple = (") == 1, (
        "D76: 提交语义词表只准一份定义（重复造轮子）"
    )
    assert "动作语义族命中" in action, "D76: 缺失「动作语义族命中」分支"


# ────────────────────────── D71 ──────────────────────────


def test_d71_no_handler_passes_flat_check_to_verifier():
    """断言参数嵌套在 check 子对象内 —— 任何把外层 check 直传校验实现的写法都是
    参数丢失（静默判绿）根源。全部必须经 `_merge_check_inner` 归一。"""
    dbui = _read(_DBUI)
    offenders = re.findall(r"await\s+verify_\w+\(\s*page\s*,\s*check\s*\)", dbui)
    assert offenders == [], (
        "D71: 以下调用未做参数归一（params 丢失→假绿）: %s" % offenders
    )


def test_d71_single_normalizer_is_used():
    """参数归一只允许一份实现（`_merge_check_inner`），且被各参数消费者引用。"""
    dbui = _read(_DBUI)
    assert "def _merge_check_inner(check, inner) -> dict:" in dbui, (
        "D71: 归一函数必须存在且唯一"
    )
    assert dbui.count("def _merge_check_inner(") == 1, (
        "D71: 参数归一只准一份实现（禁止重复造轮子）"
    )
    assert dbui.count("_merge_check_inner(check, inner)") >= 8, (
        "D71: 归一点过少，疑有处理器漏接"
    )


# ────────────────────────── D72 ──────────────────────────
# 声明目标有两种形态（引用式 `name=<key>` / 自然语言"按名称搜索"）。形态判据与
# 定位实现此前只在结构化路径（browser_tools）存在，Agent/guided 路径另起一套语义
# 匹配且**无前缀解析** → 引用式 hint 恒 0 命中 → 声明动作落不了地 → 假 ERROR。

_HINT = "src/pipeline/steps/step4/browser_tools/_hint_utils.py"


def test_d72_structured_hint_predicate_is_single_source():
    hint = _read(_HINT)
    assert "def is_structured_hint(" in hint, (
        "D72: 形态判据必须存在（is_structured_hint）"
    )
    assert hint.count("def is_structured_hint(") == 1, "D72: 形态判据只准一份实现"
    # 执行端不得各自定义前缀表（重复造轮子 → 判据漂移）
    action = _read(_ACTION)
    assert "_STRUCTURED_HINT_PREFIXES" not in action, (
        "D72: Agent 路径不得自建结构化前缀表，必须复用 _hint_utils 单一事实源"
    )


def test_d72_guided_paths_dispatch_structured_hint_first():
    """fill / click 两条路径都必须**先**按结构化形态定位，语义匹配只作回退。"""
    action = _read(_ACTION)
    assert action.count("_locate_declared_structured(page, target)") >= 2, (
        "D72: _guided_fill 与 _guided_click 都必须分派结构化定位"
    )
    idx_fill = action.index("async def _guided_fill(")
    idx_click = action.index("async def _guided_click(")
    # 结构化分派必须出现在各自函数体内、且早于语义兜底关键词
    assert "_locate_declared_structured" in action[
        idx_fill:action.index("页面无input元素", idx_fill)
    ], "D72: _guided_fill 的结构化分派必须早于语义兜底"
    assert "_locate_declared_structured" in action[
        idx_click:action.index('page.get_by_role("button", name=target)', idx_click)
    ], "D72: _guided_click 的结构化分派必须早于 getByRole 语义策略"


# ────────────────────────── D73 ──────────────────────────
# 拒绝型用例（低权执行受限操作被拒）里，"声明动作的目标不存在"是**最小权限生效**的
# 证据，不是 harness 失败；结论必须交用例自身的结构化 oracle。极性由 Step3 权威
# 判据一次判定并透传，执行端只读不推。


def test_d73_denial_polarity_is_threaded_end_to_end():
    assert '"expects_denial"' in _read(_EXECUTOR), (
        "D73: _agent_executor 必须把 expects_denial 写入 _execution_guide"
    )
    assert "case_expects_denial" in _read(_EXECUTOR), (
        "D73: 极性必须取 Step3 权威判据，禁止执行端重推"
    )
    assert 'expects_denial=bool(_guide.get("expects_denial"))' in _read(_EXPLORER), (
        "D73: explorer 必须把极性透传给 _execute_guided"
    )
    assert "expects_denial: bool = False," in _read(_ACTION), (
        "D73: _execute_guided 必须接收 expects_denial 形参"
    )


def test_d73_unreachable_declared_action_is_evidence_not_failure():
    action = _read(_ACTION)
    assert "声明动作不可达(拒绝型，交结构化断言裁决)" in action, (
        "D73: 拒绝型用例的动作不可达必须记为证据"
    )
    assert action.count("_declared_phase and expects_denial") >= 2, (
        "D73: fill/click 两条失败路径都必须受极性保护"
    )
    # 保护只覆盖「声明动作」段，启发式 ② 块的合成步骤不可达仍须诚实报错
    assert "_declared_phase = False" in action, "D73: 声明段结束后必须复位保护窗口"


# ────────────────────────── D74 ──────────────────────────
# 受限控件（input[type=number/date/…]）原生 fill 只接受合法值，而负向用例的**激励
# 恰恰是非法值** → 原生 fill 被拒会让"施加非法值"永远落不了地 → 负向校验用例系统性
# 假失败。必须由 DOM 注入值并派发事件（绕过客户端约束验证服务端校验的标准手段）。


def test_d74_restricted_widget_rejected_fill_falls_back_to_dom_injection():
    action = _read(_ACTION)
    assert "Cannot type text into input[" in action, (
        "D74: 必须识别「受限控件拒绝原生 fill」这一形态"
    )
    assert "dispatchEvent(new Event('input', {bubbles: true}))" in action, (
        "D74: DOM 注入必须派发 input/change 事件，否则页面/服务端校验不会被触发"
    )
    idx = action.index("Cannot type text into input[")
    assert "return True" in action[idx : idx + 1200], (
        "D74: 注入成功后必须视为激励已施加（返回成功）"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
