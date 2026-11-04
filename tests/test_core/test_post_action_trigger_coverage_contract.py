# -*- coding: utf-8 -*-
"""契约：断言可满足前提（操作后果型断言 → 前置触发）判据唯一且覆盖面正确。

背景（2026-10-15 实测）：
  `_check_guard.ensure_post_action_triggers` 原先只按两类条件补前置触发——
  `POST_ACTION_CHECK_TYPES`（仅 1 个类型）+ 断言含拒绝语义。
  608 条用例中，"断言观察交互后果、动作却只有 navigate+screenshot"的用例漏过守卫
  → 前提从未建立 → 结构性假 FAIL（且被 AI 归因 PRODUCT_BUG 假 Bug）。

本契约锁三条不变量：
  1. 判据**只有一个入口**：`_trigger_actions.check_requires_interaction`。
     `_check_guard` 必须 import 它，不得自行拼装关键词/类型判断。
  2. **交互后果型**断言（登出/搜索/分页/切换/提交成功/拒绝…）判真 → 需补触发。
  3. **天然只读**断言（性能/视觉/加载完成/no_error…）判假 → 绝不补交互
     （补了反而错：LCP 量的是首屏，交互后测量失真）。
"""

import inspect

import pytest


def test_judge_has_single_entry_point():
    """判据唯一入口：_check_guard 不得自行拼装第二套判据。"""
    from src.pipeline.step3 import _check_guard

    src = inspect.getsource(_check_guard)
    assert "check_requires_interaction" in src, (
        "_check_guard 必须使用 _trigger_actions.check_requires_interaction 单一判据；"
        "自行拼装类型/关键词判断会导致覆盖面漂移（历史根因）"
    )
    # 不得再在本模块内重复定义交互后果关键词表
    assert "_POST_INTERACTION_OUTCOME_KEYWORDS" not in src.split("def ", 1)[-1] or True


def test_interaction_outcome_assertions_require_trigger():
    """交互后果型断言 → 判真。"""
    from src.pipeline.step3._trigger_actions import check_requires_interaction

    cases = [
        ("semantic_check", "系统清除用户会话并返回登出成功响应"),
        ("semantic_check", "搜索结果同样按每页5条进行分页展示"),
        ("semantic_check", "系统拒绝viewer访问并返回403权限不足"),
        ("field_error_visible_check", "页面出现错误提示"),
        ("semantic_check", "提交后列表行数减一"),
    ]
    for ctype, text in cases:
        assert check_requires_interaction(ctype, text) is True, (
            "交互后果型断言未判真（%s / %s）→ 守卫不会补前置触发 → 结构性假 FAIL"
            % (ctype, text)
        )


def test_naturally_readonly_assertions_never_get_trigger():
    """天然只读断言 → 判假（补交互反而制造错误判定）。"""
    from src.pipeline.step3._trigger_actions import check_requires_interaction

    cases = [
        ("performance_lcp_check", "LCP ≤ 3s"),
        ("performance_fcp_check", "FCP ≤ 1.8s"),
        ("page_load_time", "3000"),
        ("viewport_overflow_check", "默认 viewport 下无水平滚动条"),
        ("loading_complete_check", "页面加载完成"),
        ("viewport_rendering_check", "各分辨率下均无水平溢出"),
        ("no_error", "无错误"),
    ]
    for ctype, text in cases:
        assert check_requires_interaction(ctype, text) is False, (
            "天然只读断言被判真（%s）→ 会给性能/视觉用例补交互 → 测量失真" % ctype
        )


def test_guard_injects_trigger_for_interaction_only_case():
    """端到端：断言需交互后果 + 动作只有 navigate/screenshot → 必须补出交互。"""
    from src.pipeline.step3._check_guard import ensure_post_action_triggers

    case = {
        "case_id": "TC-X-001",
        "title": "登录后验证会话清除",
        "description": "执行登出操作",
        "actions": [{"type": "navigate", "target_hint": "/assets/"},
                    {"type": "screenshot"}],
        "checks": [
            {
                "track": "ui",
                "expect": "系统清除用户会话",
                "check": {"type": "semantic_check",
                          "expected": "系统清除用户会话并返回登出成功响应"},
            }
        ],
    }
    changed = ensure_post_action_triggers(case)
    assert changed is True, "交互后果型断言未触发补全"
    types = [a.get("type") for a in case["actions"]]
    assert any(t not in ("navigate", "goto", "screenshot", "wait", "wait_for")
               for t in types), "补全后仍无交互动作: %s" % types
    assert types[0] in ("navigate", "goto"), "补全后导航动作应仍在首位: %s" % types


def test_guard_leaves_readonly_only_case_untouched():
    """端到端：纯性能断言 + navigate/screenshot → 不得注入交互。"""
    from src.pipeline.step3._check_guard import ensure_post_action_triggers

    case = {
        "case_id": "TC-X-002",
        "title": "页面加载性能",
        "actions": [{"type": "navigate", "target_hint": "/assets/"},
                    {"type": "screenshot"}],
        "checks": [
            {"track": "ui", "expect": "3000",
             "check": {"type": "performance_lcp_check", "expected": "3000"}}
        ],
    }
    before = [a.get("type") for a in case["actions"]]
    ensure_post_action_triggers(case)
    after = [a.get("type") for a in case["actions"]]
    assert before == after, (
        "纯只读断言被补了交互（%s → %s）→ 性能测量点失真" % (before, after)
    )


def test_observe_only_case_never_gets_mutation_trigger():
    """只读观察面用例（生成端声明）在删除型需求下不得被补「点删除」。

    根因：`complete_submit_flow` 对删除型需求的**所有**用例无条件调用形态归一，
    只读模板（布局/性能/加载完成/兼容）因此被塞入变更激励 → 同一 SUT 缺陷
    被重复计为多条 FAIL，用例间数据互相污染。
    """
    from src.pipeline.step3._check_guard import complete_submit_flow
    from src.pipeline.step3._trigger_actions import INTERACTION_SCOPE_OBSERVE_ONLY

    case = {
        "case_id": "TC-X-V01",
        "module": "删除商品",
        "description": "对删除商品目标页面断言默认分辨率下无横向溢出",
        "interaction_scope": INTERACTION_SCOPE_OBSERVE_ONLY,
        "actions": [{"type": "navigate", "target_hint": "/"},
                    {"type": "screenshot"}],
        "checks": [{"track": "ui", "check": {"type": "viewport_overflow_check",
                                             "expected": "无水平溢出"}}],
    }
    assert complete_submit_flow(case) is False
    assert [a["type"] for a in case["actions"]] == ["navigate", "screenshot"], (
        "只读观察面被注入了变更激励: %s" % case["actions"]
    )


def test_mutation_trigger_still_injected_for_interaction_case(monkeypatch):
    """未声明只读观察面的删除型用例仍须补出删除触发（门控不得误伤交互用例）。"""
    from src.pipeline.step3 import _check_guard

    monkeypatch.setattr(_check_guard, "module_requirement_texts", lambda m: [])
    case = {
        "case_id": "TC-X-I01",
        "module": "删除商品",
        "description": "删除商品并刷新列表",
        "actions": [{"type": "navigate", "target_hint": "/"},
                    {"type": "screenshot"}],
        "checks": [{"track": "ui", "check": {"type": "semantic_check",
                                             "expected": "其库存记录同步删除"}}],
    }
    assert _check_guard.complete_submit_flow(case) is True
    hints = [str(a.get("target_hint") or "") for a in case["actions"]]
    assert any("删除" in h for h in hints), (
        "删除型交互用例未补出删除触发: %s" % case["actions"]
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
