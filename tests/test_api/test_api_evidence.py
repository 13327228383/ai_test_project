# -*- coding: utf-8 -*-
"""证据完整性回归测试：API 直调执行器必须把"实际观测"回写进结构化证据。

背景（2026-08-30 修复）：
- 此前 api_only_executor 落库的 details 仅含意图层断言(target/assertion/body=null)，
  不含实际 HTTP 状态/观测结果 → 从 DB 无法复算 PASS/FAIL，expected/actual 恒空，
  被质疑为假绿。
- 修复：每次请求后把 observed_status / observed_evidence / observed_passed /
  observed_body / actual / _resp_status 回写进 api_check_detail；expected 用意图断言，
  actual 取各端点实际观测状态。满足"PASS 必须带可复现证据"闭环。

通用性：所有 API-only 用例（api_demo/promo_demo/storefront 等）跨系统复用此路径。
"""
import sys

import pytest

# ─────────────────────────────────────────────────────────────
# 契约1: _verify_single_assertion 返回 (passed, evidence, severity)
# ─────────────────────────────────────────────────────────────


def test_verify_status_pass():
    from src.pipeline.api_only_executor import _verify_single_assertion

    ok, ev, sev = _verify_single_assertion(
        {"status": 200, "body_text": "{}"}, "返回状态码 200"
    )
    assert ok is True
    assert sev == "pass"
    assert "200" in ev


def test_verify_status_fail():
    from src.pipeline.api_only_executor import _verify_single_assertion

    ok, ev, sev = _verify_single_assertion(
        {"status": 401, "body_text": '{"code":40101}'}, "返回状态码 400"
    )
    assert ok is False
    assert sev == "fail"


# ─────────────────────────────────────────────────────────────
# 契约2: 证据完整性 — 观测结果必须回写进 detail（Q2 核心修复）
# ─────────────────────────────────────────────────────────────


def test_observed_evidence_written_to_detail(monkeypatch):
    """执行器应把实际观测 status/body 写进 detail，而非只留意图断言。"""
    from src.pipeline import api_only_executor as mod
    from src.core.config_loader import config as _cfg
    # ★ 注入 base_url（运行时覆盖，函数内 `config.target_url` 取到它，点击不会走
    #   bare target 分支）；测试结束后恢复，避免污染全局配置。
    _cfg._set_runtime("TARGET_URL", "http://127.0.0.1:99999")
    # ★ 先确保目标模块导入，simulate 落库桩时才不会 KeyError
    from src.users import manager as _manager_import

    captured = {}

    # 伪造 HTTP 请求返回真实观测
    def fake_make_http_request(url, method="GET", cookies=None, body=None):
        if "/health" in url:
            return {"status": 200, "body_text": '{"service":"api_demo","status":"ok"}'}
        if "login" in url:
            return {"status": 200, "body_text": '{"data":{"token":"t"}}'}
        return {"status": 404, "body_text": "not found"}

    # 伪造登录：直接给出 cookie（避免 Playwright 登录）
    def fake_try_api_login(*a, **k):
        return {}

    monkeypatch.setattr(mod, "_make_http_request", fake_make_http_request)
    monkeypatch.setattr(mod, "_try_api_login", fake_try_api_login)

    # 拦截落库，捕获证据（status 是位置参数，需显式收进捕获字典）
    import json

    def fake_record_execution(user, project, cid, status, **kw):
        captured[cid] = {"status": status, "user": user, "project": project, **kw}

    monkeypatch.setattr(_manager_import, "record_execution", fake_record_execution)

    intent = {
        "case_id": "EVD-001",
        "payload": {
            "verification_plan": {
                "api": [
                    {"assertion": "返回状态码 200", "target": "GET /health",
                     "method": "GET", "evidence": "api_status", "strictness": "high"}
                ]
            }
        },
    }
    res = mod.execute_api_only_intents(
        [intent], user_id="admin", project_name="demo_test"
    )
    assert res["passed"] == 1

    kw = captured["EVD-001"]
    assert kw["status"] == "PASS"
    # 关键: expected 是意图断言，actual 是观测状态，details 含 observed_* 全证据
    assert "返回状态码 200" in kw["expected"]
    assert kw["actual"] == "200"
    det = json.loads(kw["details"])
    assert det[0]["observed_status"] == 200
    assert det[0]["observed_passed"] is True
    assert "observed_body" in det[0]
    assert "ok" in det[0]["observed_body"]


def test_observed_evidence_written_on_fail(monkeypatch):
    """FAIL 时同样回写观测结果（HTTP≠期望），保证失败可复现分析。"""
    from src.pipeline import api_only_executor as mod
    from src.core.config_loader import config as _cfg
    # ★ 注入 base_url，使用例走真实"带目标"分支（否则 bare target 直接 FAIL）
    _cfg._set_runtime("TARGET_URL", "http://127.0.0.1:99999")
    from src.users import manager as _manager_import

    captured = {}

    def fake_make_http_request(url, method="GET", cookies=None, body=None):
        return {"status": 404, "body_text": "not found"}

    def fake_try_api_login(*a, **k):
        return {}

    monkeypatch.setattr(mod, "_make_http_request", fake_make_http_request)
    monkeypatch.setattr(mod, "_try_api_login", fake_try_api_login)

    def fake_record_execution(user, project, cid, status, **kw):
        captured[cid] = {"status": status, "user": user, "project": project, **kw}

    monkeypatch.setattr(_manager_import, "record_execution", fake_record_execution)

    intent = {
        "case_id": "EVD-002",
        "payload": {
            "verification_plan": {
                "api": [
                    {"assertion": "返回状态码 200", "target": "GET /books",
                     "method": "GET", "evidence": "api_status", "strictness": "high"}
                ]
            }
        },
    }
    res = mod.execute_api_only_intents(
        [intent], user_id="admin", project_name="demo_test"
    )
    assert res["failed"] == 1

    kw = captured["EVD-002"]
    assert kw["status"] == "FAIL"
    assert kw["actual"] == "404"
    det = __import__("json").loads(kw["details"])
    assert det[0]["observed_status"] == 404
    assert det[0]["observed_passed"] is False


# ─────────────────────────────────────────────────────────────
# 契约3: expected 拼接 — 意图断言原文必须可读
# ─────────────────────────────────────────────────────────────


def test_expected_is_assertion_text(monkeypatch):
    """expected 应为结构化意图断言文本（可复现期望）。"""
    from src.pipeline.api_only_executor import _verify_single_assertion, _parse_api_assertion

    parsed = _parse_api_assertion("返回状态码 200")
    assert parsed["type"] == "status"
    assert 200 in parsed["params"]["expected"]

    ok, ev, sev = _verify_single_assertion({"status": 200, "body_text": "x"}, "返回状态码 200")
    assert ok and sev == "pass"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))