# -*- coding: utf-8 -*-
"""单元测试：Agent 探索成功后业务 checks 验证失败的失败归类（2026-09-02 根因修复）。

背景（用户质疑：执行失败的原因是否充分客观）：
  AgentExplorer 路径此前只在「初始 Agent 探索失败」时调用 classify_failure，
  「探索成功但探索后业务 checks 断言失败」的用例 _failure_cat 恒为 ""，
  → FAIL 记录无 failure_category，无法客观归因（本记录 id=1358 实证）。

修复: 新增 _classify_checks_failure，按 checks 结果统一归类：
  - UNSUPPORTED_CHECK / 断言分派异常 → HARNESS_ERROR(ERROR)
  - 网络/超时 → ENVIRONMENT_ERROR(ERROR)
  - 其余业务断言失败 → PRODUCT_BUG(FAIL)

本测试只测纯函数，不依赖任何靶子/浏览器。
"""
from src.pipeline.steps.step4.browser_core.single_mixins import _agent_checks as ac


class _Engine:
    def __init__(self, net_err=False):
        self._last_network_error = net_err


def _verify(checks=None, summary="", net_err=False):
    return {
        "all_passed": False,
        "checks": checks or [],
        "summary": summary,
        "replay_skipped": False,
    }


def test_checks_failure_unsupported_check_harness_error():
    """引擎不支持的 check 类型 → 引擎能力缺失 HARNESS_ERROR(ERROR)，不得归 PRODUCT_BUG。"""
    vr = _verify(checks=[{"reason": "UNSUPPORTED_CHECK", "passed": False}])
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert cat == "HARNESS_ERROR"
    assert status == "ERROR"


def test_checks_failure_dispatch_exception_harness_error():
    """共享断言分派异常 → 引擎问题 HARNESS_ERROR。"""
    vr = _verify(summary="[FAIL] 共享断言分派异常(boom)")
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert cat == "HARNESS_ERROR"
    assert status == "ERROR"


def test_checks_failure_network_error_environment():
    """网络错误信号 → ENVIRONMENT_ERROR(ERROR)。"""
    vr = _verify(checks=[{"reason": "", "passed": False}], summary="[FAIL] text_contains: 未找到")
    cat, status = ac._classify_checks_failure(vr, _Engine(net_err=True))
    assert cat == "ENVIRONMENT_ERROR"
    assert status == "ERROR"


def test_checks_failure_timeout_environment():
    """断言阶段超时 → ENVIRONMENT_ERROR(ERROR)。"""
    vr = _verify(summary="[FAIL] element_exists: Timeout 30s exceeded")
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert cat == "ENVIRONMENT_ERROR"
    assert status == "ERROR"


def test_checks_failure_business_mismatch_product_bug():
    """业务断言失败（页面未渲染期望内容）→ 被测系统缺陷 PRODUCT_BUG(FAIL)。"""
    vr = _verify(
        checks=[{"reason": "", "passed": False}],
        summary="UI: [FAIL] text_contains: 找到0个匹配文本节点; [PASS] no_error: 未发现错误提示元素",
    )
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert cat == "PRODUCT_BUG"
    assert status == "FAIL"


def test_checks_failure_empty_vr_never_fake_pass():
    """空 verify_result 兜底 → 默认 PRODUCT_BUG(FAIL)，绝不静默 PASS/留空。"""
    cat, status = ac._classify_checks_failure({}, _Engine())
    assert cat == "PRODUCT_BUG"
    assert status == "FAIL"


# ──────────────────────────────────────────────
# ⊘(N/A 跳过) 非判定态不得驱动 FAIL（2026-10-07 根因修复）
#   根因: 判定语义曾被下游从**渲染文本**重推，把引擎自述"不适用"的
#   `field_error_visible_check:⊘(N/A 跳过)` 当成"已执行且给出否定结论"
#   → 假 PRODUCT_BUG（实测 TC-REQ-051-N01/V02、TC-REQ-045-V02）。
#   修复: 判定读结构化权威 `_track_protocol.is_verdict`；三值元数据
#   （has_negative_verdict/no_verdict_only）由 verify_step 聚合边界透传。
# ──────────────────────────────────────────────
def _skip_check():
    return {
        "type": "field_error_visible_check",
        "passed": True,
        "status": "SKIP",
        "skipped": True,
        "skip_reason": "no_applicable_form",
        "evidence": "页面无可产生字段级错误的表单/输入控件，字段级错误断言不适用(N/A 跳过)",
    }


def _vr_with_ui(checks, passed, has_negative, no_verdict_only):
    from src.pipeline.steps.step4.verification._track_protocol import (
        aggregate_check_results,
    )

    _agg = aggregate_check_results(checks)
    assert _agg["has_negative"] == has_negative
    assert _agg["no_verdict_only"] == no_verdict_only
    return {
        "all_passed": False,
        "summary": "字段级错误断言不适用",
        "ui": {
            "passed": passed,
            "evidence": "field_error_visible_check:⊘(N/A 跳过)",
            "checks": checks,
            "has_positive_verdict": _agg["has_positive"],
            "has_negative_verdict": _agg["has_negative"],
            "no_verdict_only": _agg["no_verdict_only"],
        },
        "api": {"passed": True, "evidence": "未声明该轨，跳过(不计入AND)"},
        "db": {"passed": True, "evidence": "未声明该轨，跳过(不计入AND)"},
    }


def test_skip_only_verdict_is_not_product_bug():
    """唯一断言为 ⊘(N/A 跳过) → 无产品缺陷证据 → SKIP，绝不记 PRODUCT_BUG。"""
    vr = _vr_with_ui([_skip_check()], passed=False, has_negative=False,
                     no_verdict_only=True)
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert (cat, status) == ("ENGINE_COVERAGE_GAP", "SKIP")


def test_real_negative_verdict_still_product_bug():
    """对照：真实负向判定（✗）仍须判 PRODUCT_BUG(FAIL)，修复不得放过真缺陷。"""
    _fail = {
        "type": "field_error_visible_check",
        "passed": False,
        "evidence": "未发现可见字段级错误提示(扫描33个选择器)",
    }
    vr = _vr_with_ui([_fail], passed=False, has_negative=True, no_verdict_only=False)
    cat, status = ac._classify_checks_failure(vr, _Engine())
    assert (cat, status) == ("PRODUCT_BUG", "FAIL")


# ──────────────────────────────────────────────
# _finalize_agent_failure：权威需求未实现语义覆盖（2026-09-02 根因修复）
#   背景: _agent_executor 对权威需求未实现以单步 assert 直接返回
#   （_req_unimplemented=True），失败链路仅 1 步 → classify_failure 误判
#   HARNESS_ERROR(ERROR)。需求权威性语义应覆盖之 → PRODUCT_BUG(FAIL)。
# ──────────────────────────────────────────────
def test_finalize_req_unimplemented_overrides_harness_error():
    """权威需求未实现 → 覆盖 classify_failure 的 HARNESS_ERROR 为 PRODUCT_BUG(FAIL)。"""
    cat, status = ac._finalize_agent_failure(
        "HARNESS_ERROR", "ERROR", {"_req_unimplemented": True}
    )
    assert cat == "PRODUCT_BUG"
    assert status == "FAIL"


def test_finalize_req_unimplemented_overrides_any():
    """权威需求未实现 → 无论 classify_failure 判什么，一律 PRODUCT_BUG(FAIL)。"""
    for cat_in, st_in in (("HARNESS_ERROR", "ERROR"), ("PRODUCT_BUG", "FAIL"),
                          ("ENVIRONMENT_ERROR", "ERROR")):
        cat, status = ac._finalize_agent_failure(
            cat_in, st_in, {"_req_unimplemented": True}
        )
        assert cat == "PRODUCT_BUG"
        assert status == "FAIL"


def test_finalize_no_flag_keeps_result():
    """无 _req_unimplemented 标记 → 原样透传 classify_failure 结果。"""
    assert ac._finalize_agent_failure("PRODUCT_BUG", "FAIL", {}) == ("PRODUCT_BUG", "FAIL")
    assert ac._finalize_agent_failure("HARNESS_ERROR", "ERROR", None) == ("HARNESS_ERROR", "ERROR")


# ──────────────────────────────────────────────
# _is_authoritative_requirement：需求权威性单一事实源（2026-09-02 通用性修复）
#   权威需求 = 有可追溯 req_id 且非隐含（is_implicit=0）。
#   所有 SKIP 判决路径统一走本函数，杜绝"同一需求不同判决"的补丁式差异。
# ──────────────────────────────────────────────
from src.pipeline.steps.step4.browser_core.single_mixins import _base_classify as _bc


def test_authoritative_reads_intent_top_level():
    """真实生产形态：req_id/is_implicit 在 intent 顶层 → 权威。"""
    intent = {"req_id": "REQ-005", "is_implicit": False, "_explore_task": {}}
    assert _bc._is_authoritative_requirement(intent, {}) is True


def test_authoritative_reads_explore_task():
    """req_id 在 _explore_task 内（旧 payload 形态）→ 权威。"""
    intent = {"_explore_task": {"req_id": "REQ-006", "_req_implicit": False}}
    assert _bc._is_authoritative_requirement(intent, {}) is True


def test_authoritative_reads_case_meta_fallback():
    """intent/explore_task 均无 → 回退 case_meta → 权威。"""
    assert _bc._is_authoritative_requirement(
        {}, {"req_id": "REQ-007", "is_implicit": 0}
    ) is True


def test_non_authoritative_implicit():
    """隐含需求（is_implicit=True）→ 非权威。"""
    assert _bc._is_authoritative_requirement(
        {"req_id": "REQ-099", "is_implicit": True}, {}
    ) is False


def test_non_authoritative_no_req_id():
    """无 req_id（AI 臆造/无需求背书）→ 非权威。"""
    assert _bc._is_authoritative_requirement({"is_implicit": False}, {}) is False


def test_non_authoritative_empty():
    """空 intent/case_meta → 非权威。"""
    assert _bc._is_authoritative_requirement({}, {}) is False


# ──────────────────────────────────────────────
# 权威需求 + op_cache_v2 replay → 绝不 SKIP(REPLAY_NO_ACTION)（2026-09-02 修复）
#   背景: TC-REQ-005-I01 权威需求（req_id + is_implicit=0）replay 命中后被过滤
#   "成功后断言" → SKIP(REPLAY_NO_ACTION)，而同一批 REQ-006/007/008 却判 FAIL，
#   同一需求不同判决。修复: 权威需求强制 skip_replay 实时执行（_agent_executor），
#   _run_checks_for_agent 纵深防御：replay_hit + 权威需求 → 不跳过成功后断言。
# ──────────────────────────────────────────────
def _post_success_check():
    return {
        "post_success": True,
        "description": "保存成功后显示成功提示",
        "track": "ui",
        "check": {"type": "text_contains_check", "expected": "保存成功"},
    }


class _StubPage:
    pass


def _stub_engine():
    return type("E", (), {"_page": _StubPage()})()


def _run_checks_for_agent(intent, replay_hit, is_authoritative, monkeypatch):
    import asyncio

    from src.pipeline.steps.step4 import verification as _verif

    async def _fake_verify(page, objective):
        return {
            "passed": True,
            "checks": [
                {
                    "passed": True,
                    "type": "text_contains_check",
                    "evidence": "找到1个匹配文本节点",
                }
            ],
        }

    monkeypatch.setattr(_verif, "_verify_ui_objective", _fake_verify)
    # 固定 replay 断言跳过开关为开启，隔离外部 pipeline_config 状态
    import src.core.pipeline_config as _pc

    monkeypatch.setattr(_pc, "_read_config_value", lambda key, default=True: True)
    _mixin = ac._BrowserCoreAgentChecksMixin()
    return asyncio.run(
        _mixin._run_checks_for_agent(
            _stub_engine(),
            intent,
            "TC-REQ-005-I01",
            {"api_calls": []},
            replay_hit=replay_hit,
            is_authoritative=is_authoritative,
        )
    )


def test_authoritative_replay_never_skips_post_success(monkeypatch):
    """权威需求 + replay_hit → 成功后断言不被过滤（replay_skipped=False），
    绝不产生 REPLAY_NO_ACTION SKIP 掩盖需求满足性。"""
    intent = {
        "req_id": "REQ-005",
        "is_implicit": False,
        "checks": [_post_success_check()],
    }
    vr = _run_checks_for_agent(intent, True, True, monkeypatch)
    assert vr["replay_skipped"] is False
    assert "REPLAY_NO_ACTION" not in vr["summary"]
    assert "REPLAY_NO_ACTION" not in vr["ui"]["evidence"]


def test_non_authoritative_replay_skips_post_success(monkeypatch):
    """非权威需求 + replay_hit → 成功后断言被过滤（replay_skipped=True）。"""
    intent = {"checks": [_post_success_check()]}  # 无 req_id → 非权威
    vr = _run_checks_for_agent(intent, True, False, monkeypatch)
    assert vr["replay_skipped"] is True
    assert "REPLAY_NO_ACTION" in vr["summary"]
