# -*- coding: utf-8 -*-
"""local_semantics 语义相似度契约测试（D32 回归保护）。

背景：``similarity`` 被断言层、菜单层、控件定位层共用，属全局判据；
此前无任何测试覆盖，改动只能靠"跑一遍流水线"验证，回归不可控。

本测试钉死两条契约：
  1. 语义簇必须在「纯动作词 ↔ 复合文案」上生效（复合词取不到簇时，
     须用意图侧簇词扫描候选）——否则"查询"永远匹配不到"按名称搜索"；
  2. 防误命中红线：不允许为了命中而放宽到错配，宁可返回 None。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.ai.local_semantics import (  # noqa: E402
    _MATCH_THRESHOLD,
    _cluster_hits_text,
    _synonym_cluster,
    best_candidate,
    similarity,
)


def test_cluster_scans_composite_text():
    """意图为纯动作词、候选为复合文案时，同义簇必须生效。"""
    assert similarity("查询", "按名称搜索") >= _MATCH_THRESHOLD
    assert similarity("查找", "按名称搜索") >= _MATCH_THRESHOLD


def test_no_false_alias():
    """防误命中：语义无关的词对不得因簇扫描而被拉过阈值。"""
    for op, cand in [
        ("查询", "资产名称"),
        ("查询", "价格"),
        ("查询", "分类筛选"),
        ("删除", "按名称搜索"),
        ("保存", "按名称搜索"),
    ]:
        assert similarity(op, cand) < _MATCH_THRESHOLD, (op, cand)


def test_short_token_guard():
    """极短词元（英文 <4 字符）不得参与簇扫描，防长文案里的偶然子串命中。"""
    assert _cluster_hits_text({"x", "no", "ok"}, "xenon oxide") is False
    # 用**真实**同义簇（不手写），确认簇内中文词元能命中复合文案
    cluster = _synonym_cluster("查询") or set()
    assert cluster, "COMMON_LOOKUP 未返回查询簇"
    assert _cluster_hits_text(cluster, "按名称搜索") is True
    assert _cluster_hits_text(cluster, "资产名称") is False


def test_best_candidate_picks_composite():
    """best_candidate 能在多候选中挑出语义正确的那一个。"""
    hit = best_candidate("查询", ["资产名称", "按名称搜索", "价格", "库存"])
    assert hit is not None and hit[0] == "按名称搜索"


def test_best_candidate_refuses_when_absent():
    """候选里真没有目标控件时不得硬凑。"""
    assert best_candidate("查询", ["资产名称", "价格"]) is None


def test_regression_existing_pairs():
    """既有行为护栏：子串/属性核/中英等价等历史判定不受影响。"""
    for op, cand, expect_hit in [
        ("搜索", "按名称搜索", True),
        ("名称", "资产名称", True),
        ("用户名", "username", True),
        ("登录", "login", True),
        ("删除", "确认删除", True),
        ("资产类别", "分类", True),
        ("资产名称", "价格", False),
    ]:
        got = similarity(op, cand) >= _MATCH_THRESHOLD
        assert got == expect_hit, (op, cand, got)
