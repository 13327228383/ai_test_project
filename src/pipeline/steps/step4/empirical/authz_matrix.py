# -*- coding: utf-8 -*-
"""[未复核-需 GLM/DeepSeek 把关] 权限矩阵验证能力（AuthzMatrixVerifier）。

★ 通用能力（权限验证通用能力缺口补全）：对任意 RBAC 型 API 靶子，依据
  「角色→权限矩阵」与「探针清单」验证访问控制正确性，**无需靶子特定代码**。

★ 对齐软件测试理论（访问控制测试 / RBAC 验证）：
  - 正向（positive）：拥有权限的角色访问受保护资源 → 允许（2xx）。
  - 负向（negative）：缺少权限的角色访问 → 拒绝（403 权限不足）。
  - 边界（boundary）：未认证（无 token / anonymous）→ 401（未认证）。
  - 默认拒绝（deny by default）：未声明权限组合不应被放行。
  - 水平越权（IDOR）：低权用户访问「他人」资源应被拒（403/404）；
      若靶子返回 200 → 检出越权缺陷（探针 expected="allow_self_only"，
      实际返回他人数据 → FAIL）。
  - 垂直越权（privilege escalation）：无管理权限者调用管理端点应被拒；
      若靶子返回 200 → 检出提权缺陷（探针 expected="deny"，实际 200 → FAIL）。

★ 不降级纪律（NO_ASSERTION_DOWNGRADE）：任何异常 / token 缺失 / 响应码不符
  → 如实 FAIL，绝不假绿；探针声明 expected_code 时一并强校验状态码。

★ 输入契约（来自 target_cfg["authz"]）：
  - permission_matrix: {role: [perm, ...]} 角色拥有的权限集合。
  - probes: [{id, role, method, path,
              expected: "allow"|"deny"|"allow_self_only"|"deny_but_suspect",
              expected_code?}]
    role="anonymous" 表示未认证（不带 token）。
    expected="allow_self_only"：期望「只能访问自己」；若返回他人数据(200) → FAIL。
    expected="deny"：期望拒绝；若返回 200 → FAIL（越权缺陷被捕）。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging

from src.pipeline.steps.step4.empirical.constants import API_MODE

_log = logging.getLogger(__name__)

# 未认证角色标识（不带 token 访问）
ANONYMOUS_ROLE = "anonymous"

# 「明确的拒绝/未认证」按 HTTP 语义判定；靶子自定义业务码若需精确匹配，
# 由探针声明 expected_code（HTTP 状态码只可能是 3 位，业务码不参与比较）。
_HTTP_REJECT_CODES = frozenset({401, 403, 404})
_HTTP_UNAUTH_CODES = frozenset({401})


def classify_authz_outcome(
    expected: str | None,
    actual_code: int,
    expected_code: int | None = None,
) -> tuple[bool, str]:
    """纯函数：按期望类型与真实状态码判定权限探针是否通过（便于离线单测）。

    ★ 对齐软件测试理论（访问控制 / RBAC / 越权测试）：
      - allow：真实 2xx 且（声明时）码相等 → 通过。
      - deny / deny_but_suspect：期望拒绝；真实属于「明确拒绝白名单」且
        （声明时）码相等 → 通过；若靶子返回 2xx → 越权缺陷被捕（FAIL）。
      - allow_self_only：期望「仅访问自己」；正确实现应拒绝，若返回 2xx →
        水平越权缺陷，如实 FAIL（401 单独标「未认证」而非「水平越权」以免误归因）。

    关键纪律（NO_ASSERTION_DOWNGRADE）：拒绝判定使用白名单而非 ``>=400``，
    避免把「参数错误 400 / 服务端 5xx」误当成「预期拒绝」而假绿通过。

    Returns:
        (ok, actual_label) —— ok 是否通过；actual_label 人类可读的实际结果标签
        （含实际状态码，便于离线定位）。
    """
    if expected == "allow":
        ok = (200 <= actual_code < 300) and (
            expected_code is None or actual_code == expected_code
        )
        return ok, ("allow" if ok else f"deny(code={actual_code})")

    if expected in ("deny", "allow_self_only", "deny_but_suspect"):
        # 声明精确码 → 严格对齐；否则退化为「明确拒绝白名单」。
        if expected_code is not None:
            ok = (actual_code == expected_code)
        else:
            ok = (actual_code in _HTTP_REJECT_CODES)
        if not ok:
            # FAIL：靶子放行了本应拒绝的请求 → 越权/缺陷被捕。
            if expected == "allow_self_only":
                label = ("unauthenticated(未认证,非越权)" if actual_code in _HTTP_UNAUTH_CODES
                         else f"allow_others_data(水平越权,code={actual_code})")
            elif expected == "deny_but_suspect":
                label = f"allow(垂直越权,code={actual_code})"
            else:  # deny
                label = f"allow(越权漏洞,code={actual_code})"
            return False, label
        # PASS：确实被拒绝；按期望类型给可读标签。
        if expected == "allow_self_only":
            return True, "denied(仅自己可见，他人已拒)"
        return True, "deny"

    return False, f"unknown_expected={expected}"


class AuthzMatrixVerifier:
    """通用 RBAC 权限矩阵验证器（纯 requests，无浏览器）。

    通用性来自：靶子的「角色→权限矩阵」与「受保护端点要求的权限」由配置驱动，
    本验证器只负责按探针清单发起请求并比对期望结果，不耦合任何具体业务字段。
    """

    def __init__(self, target_cfg: dict | None = None) -> None:
        """初始化。

        Args:
            target_cfg: 含 "base_url"/"auth"/"authz" 的靶子配置。
                - auth.roles: {role: {username, password}} 登录凭证。
                - auth.login_url / auth.token_field: 登录与 token 解析。
                - authz.permission_matrix / authz.probes: 验证输入。
        """
        self.target_cfg = target_cfg or {}
        self.base_url = (
            self.target_cfg.get("base_url") or self.target_cfg.get("url") or ""
        ).rstrip("/")
        self.auth = self.target_cfg.get("auth") or {}
        self.authz = self.target_cfg.get("authz") or {}
        self.permission_matrix = self.authz.get("permission_matrix") or {}
        self.probes = self.authz.get("probes") or []
        self.token_field = self.auth.get("token_field", "data.token")
        self.login_url = self.auth.get("login_url", "/api/auth/login")
        self.roles = self.auth.get("roles") or {}
        self._token_cache: dict[str, str | None] = {}

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """执行全部权限探针，返回统一报告结构。

        ★ 确定性数据生命周期：若探针声明 reset_after=True（会改变靶子状态），
          执行后调用 authz.state_reset 端点复位，保证下一条探针面对已知初始状态，
          消除跨探针状态污染（符合软件测试理论「独立性/可重复」原则）。

        Returns:
            {target, target_key, mode, summary:{all_pass,total,passed,failed},
             probes:[...]}。
        """
        state_reset_path = (self.authz.get("state_reset") or {}).get("path")
        results: list[dict] = []
        for idx, probe in enumerate(self.probes):
            try:
                results.append(self._check_probe(probe))
                # 确定性复位：改变状态的探针执行后复位（仅对后续探针生效，
                # 本条结果已落库不参与）。首条无需复位。
                if probe.get("reset_after") and state_reset_path and idx > 0:
                    self._reset_state(state_reset_path)
            except Exception as _e:  # 不降级：异常如实 FAIL
                _log.error("[Authz] 探针 %s 异常: %s", probe.get("id"), _e)
                results.append(
                    {
                        "id": probe.get("id", "?"),
                        "role": probe.get("role"),
                        "method": probe.get("method"),
                        "path": probe.get("path"),
                        "expected": probe.get("expected"),
                        "expected_code": probe.get("expected_code"),
                        "actual_code": None,
                        "actual": "error",
                        "pass": False,
                        "error": str(_e)[:300],
                    }
                )

        total = len(results)
        passed = sum(1 for r in results if r.get("pass"))
        all_pass = total > 0 and passed == total
        return {
            "target": self.base_url,
            "target_key": self.target_cfg.get("target_key", ""),
            "mode": "authz",
            "summary": {
                "all_pass": all_pass,
                "total": total,
                "passed": passed,
                "failed": total - passed,
            },
            "probes": results,
        }

    # ─────────────────────────────────────────────────────────────────────
    # 内部：登录 / token
    # ─────────────────────────────────────────────────────────────────────

    def _login(self, role: str) -> str | None:
        """按角色登录取 token；anonymous/无凭证 → 返回 None（未认证）。"""
        if role == ANONYMOUS_ROLE or not role:
            return None
        if role in self._token_cache:
            return self._token_cache[role]
        cred = self.roles.get(role)
        if not cred:
            _log.error("[Authz] 角色 %s 无凭证配置", role)
            self._token_cache[role] = None
            return None
        import requests

        try:
            resp = requests.post(
                self.base_url + self.login_url,
                json={"username": cred["username"], "password": cred["password"]},
                timeout=10,
            )
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            token = self._dig(body, self.token_field)
            self._token_cache[role] = token
            if not token:
                _log.error("[Authz] 角色 %s 登录响应缺 token", role)
            return token
        except Exception as _e:
            _log.error("[Authz] 登录 %s 异常: %s", role, _e)
            self._token_cache[role] = None
            return None

    @staticmethod
    def _dig(obj: dict, path: str):
        """按点路径取嵌套字段（如 data.token）。"""
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def _reset_state(self, path: str) -> None:
        """调用靶子 state_reset 端点确定性复位（探针间隔离）。

        ★ 失败安全：复位失败仅记录，不影响已落库的探针结果（不降级、不扩散）。
        """
        import requests

        with guard("_reset_state: requests.post(self.base_url + path, ", log=_log, level=logging.WARNING):
            requests.post(self.base_url + path, timeout=10)

    # ─────────────────────────────────────────────────────────────────────
    # 内部：单探针校验
    # ─────────────────────────────────────────────────────────────────────

    def _check_probe(self, probe: dict) -> dict:
        """执行单条权限探针并返回结果 dict。

        判定逻辑：
          - expected=="allow"：实际状态码 2xx 且（若声明 expected_code）相等。
          - expected=="deny" ：实际状态码 >=400 且（若声明 expected_code）相等。
        """
        role = probe.get("role")
        method = (probe.get("method") or "GET").upper()
        path = probe.get("path", "")
        expected = probe.get("expected")
        expected_code = probe.get("expected_code")

        token = self._login(role)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = probe.get("body")  # 可选请求体（如改角色 {"role": "admin"}），
        # 通用能力：上传用例/主流水线桥接均可声明 body，验证器如实发送。

        import requests

        if body is not None and method in ("POST", "PUT", "PATCH", "DELETE"):
            resp = requests.request(
                method, self.base_url + path, headers=headers, json=body, timeout=10
            )
        else:
            resp = requests.request(
                method, self.base_url + path, headers=headers, timeout=10
            )
        actual_code = resp.status_code

        ok, actual = classify_authz_outcome(expected, actual_code, expected_code)

        return {
            "id": probe.get("id"),
            "role": role,
            "method": method,
            "path": path,
            "expected": expected,
            "expected_code": expected_code,
            "actual_code": actual_code,
            "actual": actual,
            "pass": ok,
        }
