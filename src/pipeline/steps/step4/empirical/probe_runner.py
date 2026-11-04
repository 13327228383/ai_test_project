# -*- coding: utf-8 -*-
"""无认证 API 靶子探针执行器（verification.cases 风格）。

用于 ``auth.type == "none"`` 的纯 REST 业务/脆弱点靶（如 edu_sut / fin_sut）：

- 不打浏览器、不登录、不读 DOM、不注入 ``Authorization``。
- 直接按 ``method / path / body / expect_code`` 打端点。
- 支持 ``truth_value.expect_denied``：探针期望 SUT **正确拒绝**（如越权应 403），
  脆弱实现放行（返回 ``expect_code`` 如 200）→ 判 FAIL 并标记 ``vuln_captured``
  （即引擎成功捕获注入的缺陷，属正确行为，非引擎失效）。

这是 empirical 验证链路对「无登录 + 自有探针集」靶子的正式支持路径，
修复了此前 ``api_login_get_token`` 对非认证靶子强制登录导致 login_failed 阻断的问题。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


class ApiProbeRunner:
    """无认证 API 探针执行器（纯 requests，无 token）。"""

    def __init__(self, target_cfg: dict | None = None) -> None:
        self.target_cfg = target_cfg or {}
        self.base_url = (
            self.target_cfg.get("base_url") or self.target_cfg.get("url") or ""
        ).rstrip("/")

    def run_probe(self, case: dict) -> dict:
        """执行单条 verification.cases 探针。

        Returns:
            结果 dict：{case, method, path, expected_code, actual_code,
                       pass, vuln_captured?, detail?}。
        """
        case_id = case.get("case_id", "?")
        method = (case.get("method") or "GET").upper()
        path = case.get("path", "")
        body = case.get("body")
        expect_code = case.get("expect_code")
        truth = case.get("truth_value") or {}

        import requests

        url = self.base_url + path
        try:
            if body is not None and method in ("POST", "PUT", "PATCH", "DELETE"):
                resp = requests.request(method, url, json=body, timeout=10)
            else:
                resp = requests.request(method, url, timeout=10)
            actual = resp.status_code
        except Exception as _e:  # 网络/靶子不可用 → 如实 FAIL，不静默、不假绿
            _log.error("[ApiProbe] %s 请求异常: %s", case_id, _e)
            return {
                "case": case_id,
                "method": method,
                "path": path,
                "expected_code": expect_code,
                "actual_code": None,
                "pass": False,
                "error": str(_e)[:200],
            }

        # ★ 脆弱点探针：期望 SUT 正确拒绝（如越权应 403）。
        if truth.get("expect_denied"):
            correct = truth.get("expected_code")  # 正确实现应返回（如 403）
            denied = actual >= 400 and (correct is None or actual == correct)
            vuln = expect_code is not None and actual == expect_code
            passed = denied
            return {
                "case": case_id,
                "method": method,
                "path": path,
                "expected_code": correct,
                "expect_vuln_code": expect_code,
                "actual_code": actual,
                "pass": passed,
                "vuln_captured": (not passed) and vuln,
                "detail": (truth.get("detail", "") if not passed else ""),
            }

        # 普通业务探针：状态码等于 expect_code 即 PASS。
        passed = (expect_code is None) or (actual == expect_code)
        return {
            "case": case_id,
            "method": method,
            "path": path,
            "expected_code": expect_code,
            "actual_code": actual,
            "pass": passed,
        }
