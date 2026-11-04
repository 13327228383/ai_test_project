"""declared_actions 契约测试 —— 用例声明的动作序列必须随 _explore_task 下发。

根因（SSOT 违背）: 用例的「去哪、点什么」有两个副本 ——
  ① case_actions_json/test_steps（Step3 落库的结构化真实动作）；
  ② _explore_task.test_method（从 module+operation 文本重新猜的一套）。
Step4 只消费 ②，① 从未被读取 → 声明被丢弃、执行端靠猜 → 找不到元素即 ERROR。
本测试锁死「声明动作必须结构化下发」这一契约，防止再次断链。
"""

from __future__ import annotations

import pytest

from src.pipeline.steps.step3.case_generator._explore_task_builder import (
    _attach_declared_actions,
    _clean_module_name,
    inject_explore_task_to_cases,
)


def test_attach_normalizes_declared_actions():
    """case_actions_json 的动作序列必须被归一进 _explore_task.declared_actions。"""
    case = {
        "case_id": "TC-X-1",
        "_explore_task": {"operation": "验证角色权限控制", "module": "接口清单"},
        "case_actions_json": (
            '[{"type": "navigate", "target_hint": "资产"},'
            ' {"type": "click", "target_hint": "新增,新建,添加,创建"},'
            ' {"type": "screenshot"}]'
        ),
    }
    _attach_declared_actions(case)
    acts = case["_explore_task"]["declared_actions"]
    assert [a["type"] for a in acts] == ["navigate", "click", "screenshot"]
    assert acts[0]["target_hint"] == "资产"
    # 多候选声明保持原样（由执行端按分隔符拆开逐个试）
    assert acts[1]["target_hint"] == "新增,新建,添加,创建"


def test_attach_accepts_list_form():
    """case_actions_json 已是 list 时同样归一（落库形态可能是 JSON 字符串或列表）。"""
    case = {
        "_explore_task": {"operation": "op"},
        "case_actions_json": [{"type": "click", "target_hint": "保存"}],
    }
    _attach_declared_actions(case)
    assert case["_explore_task"]["declared_actions"] == [
        {"type": "click", "target_hint": "保存"}
    ]


def test_attach_falls_back_to_test_steps():
    """case_actions_json 缺失时退到 test_steps（同源的另一列）。"""
    case = {
        "_explore_task": {"operation": "op"},
        "test_steps": [{"type": "navigate", "target_hint": "资产"}],
    }
    _attach_declared_actions(case)
    assert case["_explore_task"]["declared_actions"][0]["type"] == "navigate"


def test_attach_is_idempotent():
    """重复调用结果一致（inject 可能被多条生成路径调用）。"""
    case = {
        "_explore_task": {"operation": "op"},
        "case_actions_json": [{"type": "click", "target_hint": "新增"}],
    }
    _attach_declared_actions(case)
    first = list(case["_explore_task"]["declared_actions"])
    _attach_declared_actions(case)
    assert case["_explore_task"]["declared_actions"] == first


def test_attach_no_actions_no_key():
    """用例未声明动作时不得凭空造 key（避免下游把空列表当"已声明"）。"""
    case = {"_explore_task": {"operation": "op"}, "case_actions_json": ""}
    _attach_declared_actions(case)
    assert "declared_actions" not in case["_explore_task"]


def test_attach_skips_string_action_noise():
    """任务型字符串动作（如 "screenshot"）被规整为 dict，非 dict 噪音被丢弃。"""
    case = {
        "_explore_task": {"operation": "op"},
        "case_actions_json": ["screenshot", 42, None],
    }
    _attach_declared_actions(case)
    acts = case["_explore_task"].get("declared_actions")
    assert acts == [{"type": "screenshot"}]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10. 接口清单", "接口清单"),
        ("5、新增资产", "新增资产"),
        ("5. 新增资产（创建需求）", "新增资产"),
        ("2. 用户与权限（安全需求）", "用户与权限"),
        ("第三章 用户登录", "用户登录"),
        ("资产", "资产"),
        ("接口清单", "接口清单"),
    ],
)
def test_clean_module_strips_doc_chapter_number(raw, expected):
    """文档章节号/需求类型括注必须剥离 —— 留着会让侧边栏菜单匹配恒不命中。"""
    assert _clean_module_name(raw) == expected


def test_inject_end_to_end_attaches_actions():
    """走 inject 主入口时也必须带上 declared_actions（两条分支都要覆盖）。"""
    # 分支一：含 explore_task、无 _explore_task
    c1 = {
        "case_id": "TC-A",
        "explore_task": {"operation": "验证角色权限控制", "module": "10. 接口清单"},
        "case_actions_json": [{"type": "navigate", "target_hint": "资产"}],
    }
    # 分支二：已有 _explore_task
    c2 = {
        "case_id": "TC-B",
        "_explore_task": {"operation": "新增资产", "module": "5. 新增资产（创建需求）"},
        "case_actions_json": [{"type": "click", "target_hint": "新增,新建"}],
    }
    inject_explore_task_to_cases([c1, c2])
    assert c1["_explore_task"]["declared_actions"][0]["target_hint"] == "资产"
    assert c2["_explore_task"]["declared_actions"][0]["target_hint"] == "新增,新建"
    # module 章节号同步被清理
    assert c1["_explore_task"]["module"] == "接口清单"
    assert c2["_explore_task"]["module"] == "新增资产"
