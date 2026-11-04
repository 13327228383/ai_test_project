# -*- coding: utf-8 -*-
from __future__ import annotations

"""api_demo 靶子侧 API 探针套件（ApiAssertionRunner）。

★ 定位：这是 **api_demo 靶子的** 业务探针（books 端点 / 业务错误码），
  随靶子走（examples/api_demo/），不属于引擎通用层 —— 引擎经
  target_cfg["probes"]["file"] 声明按文件路径动态加载（nav_seed 同款模式）。
  通用性红线：引擎树内不得出现任何靶子的端点/错误码。
  执行方式：纯 ``requests`` 打端点，不启动浏览器、不注入 cookie、不读 DOM。

★ 不降级纪律（NO_ASSERTION_DOWNGRADE，docs §6）：
  - 任何异常 / 创建响应 201 → 绝不因此判 PASS。
  - P01 创建后必须 GET 回查真实存在 + 列表 total 精确 +1（克服"仅看创建响应"
    的假 PASS）。
  - N01/B01 若后端无校验导致 total+1 → 如实 FAIL（暴露缺陷）。
  - token 处理：登录取 ``data.token``，后续请求头 ``Authorization: Bearer <token>``。
  - 内存存储每次启动重置 → 基线靠 ``GET /api/v1/books`` 的 ``total`` 实时读取。

★ 用例分派（docs §1.2 / §3.3）：
  - P01：POST /api/v1/books 合法 → GET /books/<id> 真实存在 + 列表 total==基线+1。
  - N01：POST 非法 payload → code==40001 且 total 不变。
  - B01：POST 缺 required（title 空）→ code==40001 + blocked_by="api_param_validation"。
  - A01（越权）：user 角色 DELETE /api/v1/books/<id> → code==40301。
"""

import logging
import random
import string

from src.pipeline.steps.step4.empirical.constants import (
    BLOCKED_BY_API_PARAM,
    NO_ASSERTION_DOWNGRADE,
)

_log = logging.getLogger(__name__)

# api_demo 业务错误码（与 examples/api_demo/app.py 对齐）。
CODE_OK = 0
CODE_PARAM_ERROR = 40001
CODE_FORBIDDEN = 40301

# 合法书籍创建端点。
_BOOKS_URL = "/api/v1/books"
_BOOK_DETAIL_TMPL = "/api/v1/books/%d"
_LOGIN_URL = "/api/v1/auth/login"


class ApiAssertionRunner:
    """API 模式深断言执行器（纯 requests）。"""

    def __init__(self, target_cfg: dict | None = None, token: str | None = None) -> None:
        """初始化。

        Args:
            target_cfg: 靶子配置（mode=api, url, auth.bearer_token 等）。
            token: 已登录的 Bearer token（可选；run_case 会按需登录取 admin/user）。
        """
        self.target_cfg = target_cfg or {}
        self.base_url = (self.target_cfg.get("base_url")
                         or self.target_cfg.get("url") or "").rstrip("/")
        self.auth = self.target_cfg.get("auth") or {}
        self.token_field = self.auth.get("token_field", "data.token")
        self._admin_token = token
        self._user_token = None
        # ★ 性能样本记录：每次 HTTP 调用登记，字段与 ApiTracker._recent_requests
        #   对齐，供 perf_gate collect_api 归一化（API 模式 target 实证的 perf 数据源）。
        self._api_calls: list[dict] = []
        self._MAX_API_CALLS = 1000

    # ─────────────────────────────────────────────────────────────────────
    # 内部：HTTP 请求（统一出口，登记性能样本）
    # ─────────────────────────────────────────────────────────────────────

    def _http(self, method: str, url: str, **kwargs):
        """发 HTTP 请求并登记性能样本（method 大写：POST/GET/DELETE/...）。

        返回与 ``requests.METHOD`` 一致的 Response 对象；同时把调用记入
        ``self._api_calls``，供 perf 门禁 collect_api 归一化成 API 样本。任何
        API 型靶子经本 runner 执行都会产出 perf 样本，无需靶子侧埋点。
        """
        import time

        import requests

        method = (method or "GET").upper()
        ts = time.time()
        resp = getattr(requests, method.lower())(url, **kwargs)
        try:
            body = resp.content
            resp_bytes = len(body) if body else 0
        except Exception:  # noqa: BLE001 — resp_bytes 取不到不影响性能样本
            resp_bytes = 0
        self._api_calls.append(
            {
                "url": url,
                "method": method,
                "has_response": True,
                "status": getattr(resp, "status_code", None),
                "elapsed_ms": max(int((time.time() - ts) * 1000), 0),
                "resp_bytes": resp_bytes,
            }
        )
        if len(self._api_calls) > self._MAX_API_CALLS:
            self._api_calls = self._api_calls[-self._MAX_API_CALLS :]
        return resp

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────────────────────

    def run_case(self, case: dict) -> dict:
        """执行单条深断言用例。"""
        case_id = case.get("case_id", "UNKNOWN")
        try:
            if case_id == "P01":
                return self._api_p01(case)
            if case_id == "N01":
                return self._api_negative(case, case_id, is_boundary=False)
            if case_id == "B01":
                return self._api_negative(case, case_id, is_boundary=True)
            if case_id == "A01":
                return self._api_forbidden(case)
        except Exception as _e:
            # 不降级：任何异常如实 FAIL + error 字段。
            _log.error("[ApiAssertionRunner] %s 异常: %s", case_id, _e, exc_info=True)
            return {
                "case": case_id,
                "title": f"API 用例 {case_id}",
                "assertions": [],
                "overall": "FAIL",
                "error": str(_e)[:300],
            }
        _log.error("[ApiAssertionRunner] 未知 case_id=%s", case_id)
        return {
            "case": case_id,
            "title": "未知用例",
            "assertions": [],
            "overall": "FAIL",
        }

    # ─────────────────────────────────────────────────────────────────────
    # 内部：登录 / token
    # ─────────────────────────────────────────────────────────────────────

    def _login(self, role: str = "admin") -> str | None:
        """登录取 token。role=admin 用 admin 账号；role=user 用 user 账号。

        Returns:
            token 字符串；失败返回 None（调用方如实 blocked）。
        """
        import requests

        fields = self.auth.get("login_fields", {})
        if role == "user":
            username = fields.get("user_username", "user")
            password = fields.get("user_password", "user123")
        else:
            username = fields.get("admin_username", "admin")
            password = fields.get("admin_password", "admin123")

        try:
            resp = self._http(
                "POST",
                self.base_url + _LOGIN_URL,
                json={"username": username, "password": password},
                timeout=10,
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            if body.get("code") != CODE_OK:
                _log.error(
                    "[ApiAssertionRunner] %s 登录失败 code=%s msg=%s",
                    role, body.get("code"), body.get("message"),
                )
                return None
            token = self._dig(body, self.token_field)
            if not token:
                _log.error("[ApiAssertionRunner] %s 登录响应缺 token 字段", role)
                return None
            return token
        except Exception as _e:
            _log.error("[ApiAssertionRunner] %s 登录异常: %s", role, _e)
            return None

    def _admin_token_or_login(self) -> str | None:
        if self._admin_token:
            return self._admin_token
        self._admin_token = self._login("admin")
        return self._admin_token

    def _user_token_or_login(self) -> str | None:
        if self._user_token:
            return self._user_token
        self._user_token = self._login("user")
        return self._user_token

    @staticmethod
    def _dig(obj: dict, path: str):
        """按点路径取嵌套字段（如 data.token）。"""
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    @staticmethod
    def _rand_suffix() -> str:
        return "".join(random.choices(string.digits, k=6))

    # ─────────────────────────────────────────────────────────────────────
    # P01 正向
    # ─────────────────────────────────────────────────────────────────────

    def _api_p01(self, case: dict) -> dict:
        """P01：POST 合法书籍 → GET 回查真实存在 + 列表 total==基线+1。"""
        import requests

        result: dict = {
            "case": "P01",
            "title": "API 正向：新增书籍真实存在",
            "assertions": [],
            "overall": "FAIL",
        }
        token = self._admin_token_or_login()
        if not token:
            result["error"] = "admin 登录失败，无法取得 token"
            return result

        headers = {"Authorization": f"Bearer {token}"}
        unique_title = "验证书籍" + self._rand_suffix()
        payload = {
            "title": unique_title,
            "author": "empirical",
            "price": 12.5,
            "stock": 3,
        }
        result["unique_value"] = unique_title

        try:
            # 基线 total（实时读，内存存储不硬编码）
            base_total = self._get_book_total(headers)
            result["baseline_total"] = base_total

            # POST 创建
            resp = self._http(
                "POST",
                self.base_url + _BOOKS_URL, json=payload, headers=headers, timeout=10
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            created_id = self._dig(body, "data.id")
            result["created_id"] = created_id

            a1 = {
                "name": "创建返回 201 且 data.id 非空",
                "expected": 201,
                "actual": resp.status_code,
                "pass": resp.status_code == 201 and bool(created_id),
            }

            # ★ 深断言：GET /books/<id> 真实存在（克服"仅看创建响应"假 PASS）
            exists = False
            if created_id:
                g = self._http(
                    "GET",
                    self.base_url + _BOOK_DETAIL_TMPL % int(created_id),
                    headers=headers, timeout=10,
                )
                gbody = g.json() if g.headers.get("content-type", "").startswith(
                    "application/json"
                ) else {}
                exists = gbody.get("code") == CODE_OK and self._dig(
                    gbody, "data.title"
                ) == unique_title
            a2 = {
                "name": "GET /books/<id> 真实存在",
                "expected": "exists",
                "actual": "exists" if exists else "missing",
                "pass": exists,
            }

            # ★ 深断言：列表 total == 基线+1
            new_total = self._get_book_total(headers)
            result["new_total"] = new_total
            a3 = {
                "name": "列表 total == 基线+1",
                "expected": base_total + 1,
                "actual": new_total,
                "pass": new_total == base_total + 1,
            }

            result["assertions"] = [a1, a2, a3]
            result["overall"] = (
                "PASS" if (a1["pass"] and a2["pass"] and a3["pass"]) else "FAIL"
            )
        except Exception as _e:
            _log.error("[P01] 执行异常: %s", _e, exc_info=True)
            result["error"] = str(_e)[:300]
            result["overall"] = "FAIL"
        return result

    # ─────────────────────────────────────────────────────────────────────
    # N01 / B01 负向
    # ─────────────────────────────────────────────────────────────────────

    def _api_negative(self, case: dict, case_id: str, is_boundary: bool) -> dict:
        """N01/B01：POST 非法/缺 required → code==40001 且 total 不变。

        B01（is_boundary=True，缺 required）：额外标记 blocked_by="api_param_validation"。
        """
        import requests

        result: dict = {
            "case": case_id,
            "title": "API 负向/边界校验",
            "assertions": [],
            "overall": "FAIL",
        }
        token = self._admin_token_or_login()
        if not token:
            result["error"] = "admin 登录失败，无法取得 token"
            return result

        headers = {"Authorization": f"Bearer {token}"}

        # 构造非法 payload
        if is_boundary:
            # B01：title 空（缺 required）
            payload = {"title": "", "author": "x", "price": 1, "stock": 0}
        else:
            # N01：price<=0 非法
            payload = {
                "title": "验证非法书籍" + self._rand_suffix(),
                "author": "x",
                "price": 0,
                "stock": 0,
            }

        try:
            base_total = self._get_book_total(headers)

            resp = self._http(
                "POST",
                self.base_url + _BOOKS_URL, json=payload, headers=headers, timeout=10
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            code = body.get("code")

            a1 = {
                "name": "返回 code==40001（参数校验失败）",
                "expected": CODE_PARAM_ERROR,
                "actual": code,
                "pass": code == CODE_PARAM_ERROR,
            }

            # ★ 深断言：total 不变（若 total+1 → 如实 FAIL，暴露后端无校验）
            new_total = self._get_book_total(headers)
            result["baseline_total"] = base_total
            result["new_total"] = new_total
            a2 = {
                "name": "列表 total 不变（共 N 条）",
                "expected": base_total,
                "actual": new_total,
                "pass": new_total == base_total,
            }

            result["assertions"] = [a1, a2]

            if is_boundary and a1["pass"] and a2["pass"]:
                result["blocked_by"] = BLOCKED_BY_API_PARAM

            result["overall"] = "PASS" if (a1["pass"] and a2["pass"]) else "FAIL"
        except Exception as _e:
            _log.error("[%s] 执行异常: %s", case_id, _e, exc_info=True)
            result["error"] = str(_e)[:300]
            result["overall"] = "FAIL"
        return result

    # ─────────────────────────────────────────────────────────────────────
    # A01 越权
    # ─────────────────────────────────────────────────────────────────────

    def _api_forbidden(self, case: dict) -> dict:
        # [未复核-需 GLM/DeepSeek 把关] A01 越权用例：依赖靶子实现 40301 权限校验对齐。
        """A01：user 角色 DELETE /api/v1/books/<id> → code==40301。"""
        import requests

        result: dict = {
            "case": "A01",
            "title": "API 越权：非 admin 删除被拒",
            "assertions": [],
            "overall": "FAIL",
        }
        admin_token = self._admin_token_or_login()
        user_token = self._user_token_or_login()
        if not admin_token or not user_token:
            result["error"] = "登录失败（admin/user），无法构造越权场景"
            return result

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        user_headers = {"Authorization": f"Bearer {user_token}"}

        try:
            # admin 先创建一本（自带深断言：真实存在）
            unique_title = "越权验证书籍" + self._rand_suffix()
            c_resp = self._http(
                "POST",
                self.base_url + _BOOKS_URL,
                json={"title": unique_title, "author": "x", "price": 5, "stock": 1},
                headers=admin_headers, timeout=10,
            )
            c_body = c_resp.json() if c_resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            created_id = self._dig(c_body, "data.id")
            result["created_id"] = created_id

            if not created_id:
                result["error"] = "admin 创建书籍失败，无法构造越权删除"
                return result

            # user 尝试删除 → 期望 40301
            d_resp = self._http(
                "DELETE",
                self.base_url + _BOOK_DETAIL_TMPL % int(created_id),
                headers=user_headers, timeout=10,
            )
            d_body = d_resp.json() if d_resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            code = d_body.get("code")

            a1 = {
                "name": "user 删除返回 code==40301（越权被拒）",
                "expected": CODE_FORBIDDEN,
                "actual": code,
                "pass": code == CODE_FORBIDDEN,
            }
            result["assertions"] = [a1]
            result["overall"] = "PASS" if a1["pass"] else "FAIL"
        except Exception as _e:
            _log.error("[A01] 执行异常: %s", _e, exc_info=True)
            result["error"] = str(_e)[:300]
            result["overall"] = "FAIL"
        return result

    # ─────────────────────────────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────────────────────────────

    def _get_book_total(self, headers: dict) -> int:
        """实时读取书籍列表 total（内存存储，不硬编码基线）。"""
        import requests

        resp = self._http(
            "GET",
            self.base_url + _BOOKS_URL, headers=headers, timeout=10
        )
        body = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        data = body.get("data") or {}
        return int(data.get("total", 0))
