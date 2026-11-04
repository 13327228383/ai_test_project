# -*- coding: utf-8 -*-
"""单元测试：Step5 报告生成算法规则元信息清单。

镜像 src/pipeline/steps/step5/_rule_meta.py。
- get_rule_meta_list() 应返回报告/审计/提缺陷相关的命名规则
- 每条含 rule_id/name/description/stage/priority/version
- 关键规则必须存在（严重度分级/根因下钻/Bug 提单/性能门禁等）
"""

from src.pipeline.steps.step5._rule_meta import get_rule_meta_list


def test_rule_list_non_empty():
    rules = get_rule_meta_list()
    assert isinstance(rules, list)
    assert len(rules) >= 5


def test_each_rule_has_meta_schema():
    for r in get_rule_meta_list():
        assert r.get("rule_id")
        assert r.get("name")
        assert r.get("description")
        assert "priority" in r
        assert r.get("version")


def test_key_rules_present():
    rids = {r["rule_id"] for r in get_rule_meta_list()}
    for key in (
        "step5.severity",
        "step5.triage",
        "step5.minimal_repro",
        "step5.bug_ticket",
        "step5.perf_gate",
        "step5.manual_retest",
    ):
        assert key in rids, "缺少关键报告规则: %s" % key