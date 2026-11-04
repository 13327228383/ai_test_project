# -*- coding: utf-8 -*-
"""单元测试：Step4 检查执行算法规则元信息清单。

镜像 src/pipeline/steps/step4/verification/_rule_meta.py。
- get_rule_meta_list() 应返回 聚合层 + 自动发现的 _verify_*.py 校验器
- 每条含 rule_id/name/description/stage/priority/version（与 Step2/Step3 meta 对齐）
- 关键聚合规则必须存在（轨道推断/三轨协议/a11y/置信度等）
- stage 架构单一事实源：以源模块 _STAGE_TAXONOMY 校验，新增 stage 只改源、不改测试
  （2026-09-02 修复：原测试硬编码 ("aggregation","checker") 漏掉合法 plugin 阶段，
   导致 step4.self_healing/visual_baseline/fuzzy_match 三条真实插件规则误报违规）
"""

from src.pipeline.steps.step4.verification._rule_meta import (
    _STAGE_TAXONOMY,
    get_rule_meta_list,
)


def test_rule_list_non_empty():
    rules = get_rule_meta_list()
    assert isinstance(rules, list)
    assert len(rules) >= 15  # 聚合层 16 条 + 自动发现校验器


def test_each_rule_has_meta_schema():
    for r in get_rule_meta_list():
        assert r.get("rule_id")
        assert r.get("name")
        assert r.get("description")
        assert r.get("stage") in _STAGE_TAXONOMY
        assert "priority" in r
        assert r.get("version")


def test_stage_taxonomy_complete():
    """stage 架构必须覆盖代码真实使用的三个层级（aggregation/checker/plugin）。"""
    stages = {r["stage"] for r in get_rule_meta_list()}
    assert "aggregation" in stages
    assert "checker" in stages
    assert "plugin" in stages  # 三条浏览器执行插件规则属于合法 stage
    for s in stages:
        assert s in _STAGE_TAXONOMY


def test_plugin_rules_present():
    """插件类规则（随 plugins 系统运行的 Step4 规则）必须在清单中。"""
    pids = {r["rule_id"] for r in get_rule_meta_list() if r["stage"] == "plugin"}
    for key in ("step4.self_healing", "step4.visual_baseline", "step4.fuzzy_match"):
        assert key in pids, "缺少插件规则: %s" % key


def test_aggregation_rules_present():
    rids = {r["rule_id"] for r in get_rule_meta_list()}
    for key in (
        "step4.track_inference",
        "step4.track_protocol",
        "step4.ui_objective_assert",
        "step4.consistency",
        "step4.api_contract",
        "step4.security_xss",
        "step4.a11y",
        "step4.confidence",
    ):
        assert key in rids, "缺少关键聚合规则: %s" % key


def test_checker_rules_auto_discovered():
    checkers = [r for r in get_rule_meta_list() if r["stage"] == "checker"]
    assert checkers, "应自动发现 _verify_*.py 校验器"
    # 至少含样式/位置/性能等已知校验器
    cids = {r["rule_id"] for r in checkers}
    for key in ("step4.checker.style", "step4.checker.perf", "step4.checker.toast"):
        assert key in cids, "缺少自动发现校验器: %s" % key