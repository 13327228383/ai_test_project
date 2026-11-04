# -*- coding: utf-8 -*-
"""单元测试：Step3 导航目标「未解析」显式标注（AGENTS.md §4.17-B）。

背景：生成端在拿不到导航事实时把 target_hint 兜底成站点根 "/"，评审页于是渲染
"导航到 /"，执行端真的把浏览器带到站点根，却因断言只校验"页面加载无 404"而静默
通过（实测 550/555 条用例首步 target_hint="/"，同库 nav_graph_nodes 0 行）。

契约（本文件钉死）：
  1. 占位判据只认「无显式 url + 目标为空/根/登录页」——真实路径/页面名不算占位；
  2. 标注后台账：用例级 + 步骤级都有 nav_unresolved，target_hint 改为语义页面名，
     绝不保留 "/"；
  3. 有真实菜单事实解析成功时必须**清除**标注（不允许谎报未解析）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from src.pipeline.step3 import _navigation_resolver as nav  # noqa: E402


def _intent(actions: list, **extra) -> dict:
    base = {
        "case_id": "TC-REQ-001-I01",
        "req_id": "REQ-001",
        "module": "商品管理",
        "description": "商品列表页面展示商品",
        "actions": actions,
        "checks": [],
    }
    base.update(extra)
    return base


# ── 1. 占位判据 ──
def test_placeholder_predicate():
    assert nav.is_placeholder_nav({"type": "navigate", "target_hint": "/"}) is True
    assert nav.is_placeholder_nav({"type": "navigate", "target_hint": ""}) is True
    assert nav.is_placeholder_nav({"type": "navigate", "target_hint": "/login"}) is True
    assert nav.is_placeholder_nav({"type": "navigate"}) is True
    # 有显式 url → 是真实导航，不是占位
    assert nav.is_placeholder_nav(
        {"type": "navigate", "url": "/products", "target_hint": "/"}
    ) is False
    # 语义页面名 → 有页面身份，不是占位
    assert nav.is_placeholder_nav(
        {"type": "navigate", "target_hint": "商品管理"}
    ) is False
    # 非 navigate 动作不参与
    assert nav.is_placeholder_nav({"type": "click", "target_hint": "/"}) is False


# ── 2. 标注行为（无导航事实） ──
def test_mark_nav_unresolved_rewrites_placeholder_to_semantic_name():
    it = _intent([{"type": "navigate", "target_hint": "/"}])
    assert nav.mark_nav_unresolved(it, "nav_graph_empty") == 1
    step = it["actions"][0]
    assert step["target_hint"] == "商品管理"  # 语义页面名，不是站点根
    assert "url" not in step
    assert step["nav_unresolved"] is True
    assert step["nav_unresolved_reason"] == "nav_graph_empty"
    assert it["nav_unresolved"] is True
    assert it["nav_unresolved_reason"] == "nav_graph_empty"
    # 幂等：再次调用不再重复标注（步骤已具备页面身份）
    assert nav.mark_nav_unresolved(it, "nav_graph_empty") == 0


def test_mark_nav_unresolved_no_semantic_name_never_keeps_root():
    it = _intent([{"type": "navigate", "target_hint": "/"}], module="")
    assert nav.mark_nav_unresolved(it, "nav_graph_empty") == 1
    assert it["actions"][0]["target_hint"] == ""  # 宁可没有目标，也不伪装成 "/"


def test_clear_nav_unresolved_removes_all_levels():
    it = _intent(
        [{"type": "navigate", "url": "/products", "target_hint": "商品管理",
          "nav_unresolved": True, "nav_unresolved_reason": "no_menu_match"}],
        nav_unresolved=True,
        nav_unresolved_reason="no_menu_match",
    )
    nav.clear_nav_unresolved(it)
    assert "nav_unresolved" not in it and "nav_unresolved_reason" not in it
    step = it["actions"][0]
    assert "nav_unresolved" not in step and "nav_unresolved_reason" not in step


# ── 3. 批量解析：无导航数据 → 全部显式标注（不再静默跳过） ──
def test_batch_with_empty_nav_graph_marks_every_intent(monkeypatch):
    monkeypatch.setattr(nav, "_load_menu_nodes", lambda *a, **k: [])
    intents = [
        _intent([{"type": "navigate", "target_hint": "/"}]),
        _intent([{"type": "navigate", "target_hint": "/"}], case_id="TC-REQ-002-I01"),
    ]
    out = nav.resolve_intents_batch(intents, "SmartShop", "v1.0")
    assert len(out) == 2
    for it in out:
        assert it["nav_unresolved"] is True
        assert it["nav_unresolved_reason"] == "nav_graph_empty"
        assert it["actions"][0]["target_hint"] != "/"


# ── 4. 有真实菜单事实 → 覆写并清除标注（不谎报未解析） ──
def test_batch_with_menu_node_resolves_and_clears(monkeypatch):
    nodes = [
        {
            "page_title": "商品管理",
            "page_url": "/products",
            "page_elements": '{"buttons": [{"text": "新增商品", "region": "main"}], "inputs": []}',
        }
    ]
    monkeypatch.setattr(nav, "_load_menu_nodes", lambda *a, **k: nodes)
    it = _intent(
        [{"type": "navigate", "target_hint": "/", "nav_unresolved": True,
          "nav_unresolved_reason": "nav_graph_empty"}],
        nav_unresolved=True,
        nav_unresolved_reason="nav_graph_empty",
    )
    out = nav.resolve_intents_batch([it], "SmartShop", "v1.0")
    step = out[0]["actions"][0]
    assert step["url"] == "/products"
    assert step["target_hint"] == "商品管理"
    assert "nav_unresolved" not in step
    assert "nav_unresolved" not in out[0]
    assert "nav_unresolved_reason" not in out[0]
