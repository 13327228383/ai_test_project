# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
api_only_executor.py — API 直调测试用例执行器
===============================================
从 scheduler.py 拆分出的独立模块，负责纯 API 测试用例的快速通道执行。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过门面模式兼容
"""

import logging
import re
import time as _time

_log = logging.getLogger(__name__)

# ★ 登录缓存：避免每个用例都重新 Playwright 登录（18s/次）
_AUTH_CACHE = {}  # key: base_url → {"auth": {...}, "ts": timestamp}
_AUTH_CACHE_TTL = 600  # 10 分钟缓存

# ═══════════════════════════════════════════════════════════════
# HTTP 直调验证引擎 + 登录函数组（从 api_login_helpers re-import）
# ═══════════════════════════════════════════════════════════════
# ★ §4.3 单文件≤800行：登录函数组 + _make_http_request 拆分到 api_login_helpers.py
#   零业务逻辑变更，仅物理位置移动；re-export 保持本模块对外 API 兼容
from src.pipeline.api_login_helpers import (
    _DEFAULT_LOGIN_PATHS,
    _make_http_request,
    _try_api_login,
    _try_playwright_login,
    _try_urllib_login,
)


def _build_url(base_url: str, target: str) -> str:
    """拼接完整 URL。target 如果是完整 URL 则直接返回。

    ★ API 前缀自动探测：调用 build_url_with_auto_prefix 自动检测前端 baseURL，
      无需人工配置。详见 api_prefix_detector.py。
    """
    # 先检查 target 有效性
    if not target or target.strip().lower() in ("unknown", ""):
        return ""
    target = target.strip()
    # ★ target 可能是 "POST /login" 格式（method+path），只取 path 部分
    #   method 由调用方从 detail.method 单独传入，不应拼入 URL
    parts = target.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in (
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "PATCH",
    ):
        target = parts[1].strip()
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if not base_url:
        return ""
    # ★ base_url 可能含路径（如 http://host:port/login），只取 scheme+host+port
    #   避免拼成 http://host:port/login/dataBoard/xxx 的多层路径
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.path and parsed.path != "/":
        base_url = "%s://%s" % (parsed.scheme, parsed.netloc)
    return "%s/%s" % (base_url.rstrip("/"), target.lstrip("/"))


def _parse_api_assertion(assertion: str) -> dict:
    """将自然语言断言解析为可执行的结构化检查。

    支持模式:
      - "返回状态码 200" / "状态码 201"
      - "响应体包含 XXX" / "返回含有 YYY"（可选引号）
      - "返回 JSON code=0" / "code 为 success"
      - "成功" / "正常" / "OK"（兜底语义）

    Returns: {"type": "status"|"body_contains"|"json_field",
              "params": {...}}

    ★ 根因防御：纯数字（非 3 位状态码）判定为非法断言。
      AI 偶发把 expected_feedback 生成为 "1" / "0" 等数字（无业务语义），
      原兜底把它解析成 body_contains(text="1") → HTML body 总有 "1" →
      404/500 也会被判定 passed=True（系统性假 PASS）。
      非法断言返回不存在的状态码，使 status 类型检查 FAIL 而非误 PASS。
      通用性：任何 AI/规则生成的"伪断言"（数字/标点/空串）都在此被拦截。
    """
    a = assertion.strip()

    # ★ 防御：非法断言（纯数字/空串/单标点）→ 返回不存在的状态码，强 FAIL
    from src.core.constants import is_illegal_assertion_text
    if is_illegal_assertion_text(a):
        return {"type": "status", "params": {"expected": [-1], "expected_display": "(非法断言)"}}

    # 1. 状态码数字: "返回 200" / "状态码 201"
    m = re.search(r"(?:返回|状态码|status)\s*(\d{3})", a, re.IGNORECASE)
    if m:
        code = int(m.group(1))
        acceptable = [code]
        if code == 200:
            acceptable = [200, 201, 204]  # RESTful 放宽
        return {
            "type": "status",
            "params": {"expected": acceptable, "expected_display": str(code)},
        }

    # 2. 包含文本: "响应体/响应/返回 包含/含有 XXX"（引号可选）
    m = re.search(
        r'(?:响应体|响应|body|返回)\s*(?:包含|含有|包括)\s*[""]?(.+?)[""]?\s*$',
        a,
        re.IGNORECASE,
    )
    if m:
        text = m.group(1).strip()
        if text:
            return {"type": "body_contains", "params": {"text": text}}

    # 3. JSON 字段: "code=0" / "code 为 success" / "code: 0" / "code 是 0"
    m = re.search(r"(\w+)\s*[=＝:：为是]\s*(\S+)", a, re.IGNORECASE)
    if m:
        val = m.group(2).strip().strip('"').strip("'")
        return {"type": "json_field", "params": {"field": m.group(1), "value": val}}

    # 4. 泛化成功语义（最后兜底，防止误杀具体断言）
    if any(a.lower() == w for w in ("成功", "正常", "ok", "success")):
        return {
            "type": "status",
            "params": {"expected": [200, 201, 204], "expected_display": "2xx"},
        }

    # 5. 全文匹配兜底
    return {"type": "body_contains", "params": {"text": a}}


def _verify_single_assertion(response: dict, assertion: str) -> tuple[bool, str, str]:
    """对单个 HTTP 响应执行断言验证。

    Returns:
        (passed: bool, evidence: str, severity: str)
        severity 取值: 'pass' | 'warning' | 'fail'
        - 'pass': 断言通过
        - 'warning': 断言失败但属于软性失败（响应格式差异/字段缺失），不应阻断用例
        - 'fail': 硬性失败（HTTP状态码异常），应阻断用例
    """
    parsed = _parse_api_assertion(assertion)
    atype = parsed["type"]
    params = parsed["params"]
    http_status = response.get("status")

    # ★ 连接失败：http_status 为 0 或 None → 目标系统不可达，非产品bug
    if not http_status:
        return (
            False,
            "API请求失败 (HTTP %s, 目标系统不可达)" % (http_status or "N/A"),
            "warning",
        )

    if atype == "status":
        actual = http_status
        expected = params["expected"]
        expected_display = params.get("expected_display", str(expected))
        if actual in expected:
            return True, "HTTP %d ✓ (期望 %s)" % (actual, expected_display), "pass"
        return False, "HTTP %d ≠ 期望 %s" % (actual, expected_display), "fail"

    elif atype == "body_contains":
        text = params["text"]
        body = response.get("body_text", "")
        if text in body:
            return True, '响应体包含 "%s" ✓' % text[:50], "pass"
        # ★ 软性失败：HTTP < 500 但响应体不含预期文本 → 可能是格式差异/请求参数问题
        # 4xx 是客户端错误（测试用例生成问题），5xx 才是服务端错误（产品bug）
        if http_status < 500:
            return (
                False,
                '响应体不含 "%s" (HTTP %d, 格式/参数差异)' % (text[:50], http_status),
                "warning",
            )
        return (
            False,
            '响应体不含 "%s" (HTTP %d, 服务端错误)' % (text[:50], http_status),
            "fail",
        )

    elif atype == "json_field":
        field = params["field"]
        expected = params["value"]
        body_json = response.get("body_json") or {}
        actual = body_json.get(field) if isinstance(body_json, dict) else None
        if actual is None:
            # ★ 软性失败：字段不存在 + HTTP < 500 → 可能是响应格式差异
            if http_status < 500:
                return (
                    False,
                    "JSON 字段 %s 不存在 (HTTP %d, 格式差异)" % (field, http_status),
                    "warning",
                )
            return (
                False,
                "JSON 字段 %s 不存在 (HTTP %d, 服务端错误)" % (field, http_status),
                "fail",
            )
        # 类型宽松比较
        match = (
            str(actual).strip() == str(expected).strip()
            or str(actual).lower() == str(expected).lower()
            or (expected.isdigit() and str(actual) == expected)
        )
        if match:
            return True, "%s=%s ✓" % (field, str(actual)), "pass"
        # ★ 软性失败：字段值不匹配 + HTTP < 500 → 业务逻辑差异
        if http_status < 500:
            return (
                False,
                "%s=%s ≠ 期望 %s (HTTP %d, 业务差异)"
                % (field, str(actual), expected, http_status),
                "warning",
            )
        return (
            False,
            "%s=%s ≠ 期望 %s (HTTP %d, 服务端错误)"
            % (field, str(actual), expected, http_status),
            "fail",
        )

    return False, "无法解析断言: %s" % assertion[:60], "fail"


def _run_coro_sync(coro):
    """在同步上下文执行协程（api_only 通道是同步的，而 DB 轨权威注册器是异步的）。

    · 无运行中事件循环 → ``asyncio.run``（api_only 直调常见形态）
    · 已有运行中事件循环 → 新线程内独立 loop 执行（避免 RuntimeError: loop 已运行）
    通用性：跨系统/行业，任何"同步执行器需复用异步校验器"的场景都成立。
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    _box: dict = {}

    def _worker():
        try:
            _box["r"] = asyncio.run(coro)
        except BaseException as _e:  # noqa: BLE001
            _box["e"] = _e

    _t = threading.Thread(target=_worker)
    _t.start()
    _t.join()
    if "e" in _box:
        raise _box["e"]
    return _box.get("r")


def execute_api_only_intents(
    intents: list[dict],
    *,
    user_id: str = None,
    project_name: str = "",
    excel_handler=None,
) -> dict[str, int]:
    """快速通道：api_only 测试用例直接通过 HTTP 直调 + DB 校验完成客观验证。

    对 verification_plan.api 中的断言：解析 target 端点 → 自动登录 → HTTP 请求 → 断言校验。
    - base_url 未配置 → 降级 WARN
    - target 未知 → strictness=high 则 FAIL，否则 WARN
    - HTTP 请求失败 → strictness=high 则 FAIL，否则 WARN
    - optional 断言失败 → WARN（不阻断）
    """
    passed = 0
    failed = 0

    for intent in intents:
        cid = intent.get("case_id", "?")
        case_version_id = int(
            intent.get("_execution_version_id") or intent.get("version_id") or 0
        )
        db_checks = []
        api_checks = []

        step_checks = intent.get("_step_checks", {})
        if step_checks:
            for step_idx, checks in step_checks.items():
                db_checks.extend(checks.get("db_checks", []))
                api_checks.extend(checks.get("api_checks", []))
        vr = intent.get("verification_result", "")
        if isinstance(vr, str) and vr.strip():
            with guard("execute_api_only_intents: import json as _json", log=_log):
                import json as _json

                parsed = _json.loads(vr)
                for step_idx, checks in parsed.items():
                    db_checks.extend(checks.get("db_checks", []))
                    api_checks.extend(checks.get("api_checks", []))

        # ★ v3.0: 从 verification_plan 提取 api_checks / db_checks
        #   verification_plan 由 step4_executor 从 checks[] 自动构建
        #   api_checks: 保留结构化信息(assertion + target + evidence + strictness)，供 HTTP 直调使用
        api_check_details = []  # 结构化 dict 列表
        # ★ 2026-08-30 需求可追溯性: 持久化断言时携带 req_id 与 source，
        #   证明"断言依据来自需求"（requirement 轨: 标题/描述 → checks[]/RAG api_endpoints）。
        #   通用性: 跨系统 — 每条 API 断言都可回溯到需求来源，审计可复核。
        _trace_req_id = str(intent.get("req_id", "") or intent.get("requirement_id", "") or "").strip()
        if not db_checks and not api_checks:
            payload = (
                intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
            )
            vp = (
                payload.get("verification_plan")
                or intent.get("verification_plan")
                or {}
            )
            if isinstance(vp, str):
                try:
                    import json as _json

                    vp = _json.loads(vp)
                except Exception as _e:
                    _log.debug("[api_only_executor] 非关键异常: %s", _e, exc_info=True)
                    vp = {}
            if isinstance(vp, dict):
                # 提取 api_checks（结构化：保留 target/method 供 HTTP 直调）
                for item in vp.get("api") or []:
                    if isinstance(item, dict):
                        assertion = str(item.get("assertion") or "").strip()
                        target = str(item.get("target") or "").strip()
                        method = str(item.get("method") or "").strip().upper() or "GET"
                        if assertion:
                            api_checks.append(assertion)  # 兼容旧版字符串格式
                            api_check_details.append(
                                {
                                    "assertion": assertion,
                                    "target": target,
                                    "method": method,
                                    "evidence": str(item.get("evidence") or ""),
                                    "strictness": str(
                                        item.get("strictness") or "medium"
                                    ),
                                    # ★ 2016-08-30 需求可追溯性: source(step3_ai/rag/…)
                                    #   与 req_id 一并落库，证明断言源于需求。
                                    "source": str(item.get("source") or ""),
                                    "req_id": _trace_req_id,
                                    # ★  修复：透传请求 body（登录/新增等 JSON 请求体）
                                    "body": item.get("body") or item.get("params"),
                                }
                            )
                    elif isinstance(item, str) and item.strip():
                        api_checks.append(item.strip())
                        api_check_details.append(
                            {"assertion": item.strip(), "target": "", "source": "", "req_id": _trace_req_id}
                        )
                # 提取 db_checks（结构化或断言文本）
                for item in vp.get("db") or []:
                    if isinstance(item, dict):
                        # ★ 契约修复(2026-09-27): plan 无损透传的**已注册类型** check ——
                        #   指令语义承载于 type，必须按类型交 DB 轨注册器执行，
                        #   不得降维成字符串/SQL 兜底（否则 db_table_accessed_check 等被
                        #   verify_db 当成 unverified → 假 FAIL + 错误归因 PRODUCT_BUG）。
                        _ctype = str(item.get("type", "") or "").strip()
                        if _ctype:
                            db_checks.append(
                                {
                                    "type": _ctype,
                                    "check": item.get("check")
                                    if isinstance(item.get("check"), dict)
                                    else item,
                                    "assertion": item.get("assertion", ""),
                                }
                            )
                            continue
                        # ★ v2.1: 保留 unverified/skipped 占位检查的 dict 结构
                        _ev = item.get("evidence", "")
                        if _ev in ("unverified", "skipped"):
                            db_checks.append(
                                {
                                    "evidence": _ev,
                                    "table": "",
                                    "where": "",
                                    "expect": item.get("expect", ""),
                                    "assertion": item.get("assertion", ""),
                                }
                            )
                            continue
                        _table = str(item.get("table") or "").strip()
                        _where = str(item.get("where") or "").strip()
                        _field_check = (
                            item.get("field_check")
                            if isinstance(item.get("field_check"), dict)
                            else {}
                        )
                        if _table and _where:
                            db_checks.append(
                                {
                                    "table": _table,
                                    "where": _where,
                                    "expect": item.get("expect", ">=1"),
                                    "field_check": _field_check,
                                }
                            )
                        elif item.get("assertion"):
                            db_checks.append(str(item["assertion"]))
                    elif isinstance(item, str) and item.strip():
                        db_checks.append(item.strip())

        all_ok = True
        failure_reasons = []
        warnings = []
        _auth_blocked = (
            False  # 失败源于 401/403 且无有效认证 → 前置条件缺失(非产品缺陷)
        )
        # ★ F6 根因修复（2026-09-27）：置信度计数器必须在本用例的无条件初始化区定义。
        #   根因（UnboundLocalError 直接崩掉整个 Step4，实测 pilot 20 条 0 执行）：
        #     原代码把 `_checks_total/_checks_passed = 0` 写在 `if api_checks:` 分支内，
        #     但消费点（db 轨计数 L612 / 置信度 L632 / 假 PASS 兜底 L699）在函数级。
        #     当用例只有 db_checks、没有 api_checks 时（API 轨用例经 verification_plan
        #     归一后常见此形态），先读后写 → Python 判定为局部变量 → UnboundLocalError。
        #     影响面：任一"仅 DB 断言"的 api_only 用例即可让整个 Step4 抛异常中止
        #     （step4_executor → execute_api_only_intents 直调，异常未被吞掉），
        #     表现为 run 秒级 RUN_END 且 executions 零增量——即"跑了但没有执行记录"。
        #   通用性: 跨系统/行业 —— 计数器作用域与消费点对齐，与该用例走哪条轨无关。
        _checks_total = 0
        _checks_passed = 0
        if api_checks:
            # ★ 增强：真正执行 HTTP 校验（替代旧版纯 WARN 跳过）
            from src.core.config_loader import config as _cfg

            base_url = (_cfg.target_url or "").strip()
            http_cookies = {}
            http_auth_attempted = False
            if base_url and api_check_details:
                # 凭据唯一来源是项目配置（config.target_username/password）；
                # 缺失即跳过登录，由缺认证的 401/403 走"前置条件缺失"而非替靶子猜账号。
                _uname = _cfg.target_username
                _upwd = _cfg.target_password
                if _uname and _upwd:
                    cache_key = base_url.rstrip("/")
                    cached = _AUTH_CACHE.get(cache_key)
                    # ★ 负缓存用短 TTL（2 分钟）：目标暂时不可达/无登录表单时，
                    #   较快允许重试；正缓存仍用完整 TTL。
                    _ttl = _AUTH_CACHE_TTL
                    if cached and cached.get("neg"):
                        _ttl = min(_ttl, 120)
                    if cached and (_time.time() - cached["ts"] < _ttl):
                        http_cookies = cached["auth"]
                        _log.debug(
                            "[API直调] 使用缓存的登录凭证 (token=%s)",
                            "__token__" in http_cookies,
                        )
                    else:
                        _log.info("[API直调] 尝试登录目标系统: %s", base_url)
                        http_cookies = _try_api_login(
                            base_url, _uname, _upwd, project=project_name
                        )
                        http_auth_attempted = True
                        if http_cookies:
                            _AUTH_CACHE[cache_key] = {
                                "auth": http_cookies,
                                "ts": _time.time(),
                            }
                            has_token = "__token__" in http_cookies
                            cookie_count = sum(
                                1 for k in http_cookies if k != "__token__"
                            )
                            _log.info(
                                "[API直调] 登录结果: %d cookie, token=%s",
                                cookie_count,
                                has_token,
                            )
                        else:
                            _log.warning(
                                "[API直调] 登录失败，将以无认证模式执行 API 校验"
                            )
                            # ★ 根因修复(2026-09-21)：负结果也必须缓存，否则纯 API
                            #   靶子/目标暂时不可达时，每条用例都重复完整登录探测
                            #   （urllib+Playwright 探测 45s 超时），41 条用例被
                            #   N×45s 拖死（实测 run-slot watchdog 强制释放）。
                            #   负缓存短 TTL：目标恢复后较快自动重试。
                            _AUTH_CACHE[cache_key] = {
                                "auth": {},
                                "ts": _time.time(),
                                "neg": True,
                            }

            # ★ 置信度收集：api_exec 执行结果只落 all_ok 布尔，
            #   却从不计算/落库 confidence，导致 PASS 用例 confidence=0.0 被 KPI 聚合器
            #   平均成 0%。此处按"实际求值断言通过比例"计算置信度，与
            #   step4/_confidence.py 的多因子加权同一理念（客观证据比例→档位），通用适用。
            #   ★ F6（2026-09-27）：计数器初始化已上移到本用例的无条件初始化区（见上），
            #     此处不得再重复初始化，否则 db-only 用例仍会命中 UnboundLocalError。
            for detail in api_check_details or []:
                if not isinstance(detail, dict):
                    # 旧版字符串格式 api_checks → 降级 WARN
                    warnings.append(
                        "API断言缺少 target 信息，跳过校验: %s" % str(detail)[:60]
                    )
                    continue
                target = detail.get("target", "").strip()
                assertion = detail.get("assertion", "").strip()
                strictness = detail.get("strictness", "medium").strip()
                method = str(detail.get("method", "") or "").strip().upper() or "GET"
                # ★ 根因修复(2026-10-23): target 内嵌 method → 以 target 为准。
                #   Step3 生成 verification_plan 时，method 字段常填错/默认 GET，
                #   但 target 如 "POST /assets/<id>/delete" 正确携带 method →
                #   执行器用 GET 去调 POST 端点 → 404 假 PASS（body_contains
                #   "1" 兜底又把 404 HTML 误判为 PASS）。
                #   通用性：任何系统的 API 断言都可能出现 method/target 不一致。
                _tparts = target.split(None, 1)
                _t_method = _tparts[0].upper() if len(_tparts) == 2 and _tparts[0].upper() in (
                    "GET", "POST", "PUT", "DELETE", "PATCH",
                ) else ""
                if _t_method and _t_method != method:
                    method = _t_method
                if not assertion:
                    continue

                # 计数探测类断言（plan 的 evidence=call_count）在直调轨没有 XHR 观测，
                # 其语义等价为「该端点可调用」→ 按 HTTP 状态判定。若沿用断言文本，
                # 人类可读标题（"接口清单-REQ-054-…-接口验证"）会被当成响应体 needle
                # 重新推导，任何真实响应都不可能包含它 → 恒假失败。
                if str(detail.get("evidence") or "").strip() == "call_count":
                    assertion = "返回状态码 200"

                # ★ API 前缀自动探测：无需人工配置，自动检测前端 baseURL
                from src.pipeline.api_prefix_detector import build_url_with_auto_prefix

                url = (
                    build_url_with_auto_prefix(
                        base_url, target, project=project_name, method=method
                    )
                    if base_url
                    else ""
                )
                if url:
                    try:
                        # ★  修复：支持 verification_plan.api 携带请求 body
                        #   （detail.body / detail.params，如登录/新增接口的 JSON 请求体）
                        _req_body = detail.get("body") or detail.get("params")
                        # ★  通用性修复: 登录断言用例（target 含 login）
                        #   未显式带请求体时，按断言期望自动选凭据——
                        #   期望 2xx（登录成功场景）→ 有效凭据；期望 401/403
                        #   （错误凭据场景）→ 错误凭据。否则 POST /login 无
                        #   username/password → 401（误报），或错误凭据用例
                        #   被有效凭据覆盖（假 PASS）。
                        #   通用性: 跨系统 — 登录端点的正反用例天然需要区分凭据。
                        if not _req_body and "login" in target.lower() and _uname:
                            _exp = str(detail.get("assertion") or "")
                            if any(_c in _exp for _c in ("401", "403", "失败", "错误")):
                                _req_body = {
                                    "username": "wrong_user_%s" % _uname,
                                    "password": "wrong_pwd_%s" % _upwd,
                                }
                            else:
                                _req_body = {"username": _uname, "password": _upwd}
                        resp = _make_http_request(
                            url, method=method, cookies=http_cookies, body=_req_body
                        )
                        # ★ 通用修复 (2026-07-17): 检测 401，尝试重新登录后重试
                        # ★  根因修复：重登前先看负缓存——目标系统刚登录
                        #   失败过（无登录表单/不可达）时，逐用例 401 重登会把整个
                        #   Step4 拖成 N×45s（实测 playwright teardown 死锁超时链）。
                        #   负缓存有效期内跳过重登；过期后才允许再试一次。
                        _retry_needed = False
                        cache_key = base_url.rstrip("/") if base_url else ""
                        _neg_cached = _AUTH_CACHE.get(cache_key) or {}
                        _neg_fresh = (
                            _neg_cached.get("neg")
                            and (_time.time() - _neg_cached.get("ts", 0) < 120)
                        ) if cache_key else False
                        if resp.get("status") == 401 and base_url and _uname and _upwd:
                            if _neg_fresh:
                                _log.debug(
                                    "[API直调] %s 收到401，但登录负缓存有效(刚失败过)，跳过重登",
                                    cid,
                                )
                            else:
                                _log.warning(
                                    "[API直调] %s 收到401，尝试重新登录后重试", cid
                                )
                                _AUTH_CACHE.pop(cache_key, None)
                                new_auth = _try_api_login(
                                    base_url, _uname, _upwd, project=project_name
                                )
                                if new_auth:
                                    _AUTH_CACHE[cache_key] = {
                                        "auth": new_auth,
                                        "ts": _time.time(),
                                    }
                                    http_cookies = new_auth
                                    resp = _make_http_request(
                                        url, method=method, cookies=http_cookies, body=_req_body
                                    )
                                    _retry_needed = True
                                    _log.info(
                                        "[API直调] %s 重登后重试: status=%d",
                                        cid,
                                        resp.get("status") or 0,
                                    )
                                else:
                                    # 重登仍失败 → 写负缓存（短 TTL），避免逐用例重试
                                    _AUTH_CACHE[cache_key] = {
                                        "auth": {},
                                        "ts": _time.time(),
                                        "neg": True,
                                    }
                        passed_http, evidence, severity = _verify_single_assertion(
                            resp, assertion
                        )
                        _checks_total += 1
                        # ★  证据完整性修复: 将"实际观测"回写进结构化证据，
                        #   使 PASS/FAIL 可从 DB 单独复算（此前 details 仅含意图层面的
                        #   assertion/target，不含实际 HTTP 状态/观测 → expected/actual
                        #   恒为空、无可复现证据，被质疑为假绿）。
                        #   通用性: 所有 API-only 用例跨系统受益。
                        with guard("execute_api_only_intents: _obs_resp_body = resp.get('body_text", log=_log):
                            _obs_resp_body = resp.get("body_text") or ""
                            _obs_status = resp.get("status") or 0
                            detail["observed_status"] = _obs_status
                            detail["observed_evidence"] = evidence
                            detail["observed_passed"] = passed_http
                            detail["observed_body"] = (_obs_resp_body[:300] if _obs_resp_body else "")
                            detail["actual"] = str(_obs_status)
                            # 落库时实际观测历史: 记录每次请求观测到的事后状态
                            if isinstance(resp, dict) and resp.get("status") is not None:
                                detail["_resp_status"] = _obs_status
                        if passed_http:
                            _checks_passed += 1
                        if not passed_http:
                            if strictness == "optional" or severity == "warning":
                                # optional 断言 / 软性失败 → WARN 不阻断
                                prefix = (
                                    "API校验(soft)"
                                    if severity == "warning"
                                    else "API校验(optional)"
                                )
                                msg = "%s %s: %s" % (prefix, url, evidence)
                                warnings.append(msg)
                                _log.warning("[API直调] %s %s", cid, msg)
                            else:
                                all_ok = False
                                _st = resp.get("status") or 0
                                if _st in (401, 403) and not http_cookies:
                                    _auth_blocked = True
                                msg = "API校验失败 [%s → %d]: %s" % (url, _st, evidence)
                                failure_reasons.append(msg)
                                _log.error("[API直调] %s %s", cid, msg)
                        else:
                            _log.info("[API直调] %s ✓ %s", cid, evidence)
                    except Exception as e:
                        msg = "API请求异常 [%s]: %s" % (url, str(e)[:80])
                        _checks_total += 1  # 异常即求值失败，不记 passed
                        if strictness == "high":
                            all_ok = False
                            failure_reasons.append(msg)
                            _log.error("[API直调] %s %s", cid, msg)
                        else:
                            warnings.append(msg)
                            _log.warning("[API直调] %s %s", cid, msg)
                else:
                    # target 未知 → 一律 FAIL（不论 strictness）
                    #   断言没有可执行的目标就不是测试，跳过等于自欺欺人。
                    all_ok = False
                    _checks_total += 1  # 缺 target 求值失败，不记 passed
                    msg = "API断言缺少 target 端点，无法校验: %s" % assertion[:60]
                    failure_reasons.append(msg)
                    _log.error("[API直调] %s %s", cid, msg)

            if not base_url and (api_check_details or api_checks):
                # base_url 未配置 → 无法执行 API 校验，一律 FAIL（fail-closed）
                # ★ 2026-08-04: 用 api_checks 全集判定（verification_result 路径只填
                #   api_checks 字符串，不填 api_check_details → 原判断漏检导致 fail-open）
                all_ok = False
                msg = (
                    "未配置 target_url，%d 条 API 校验无法执行；"
                    "请在项目设置中配置被测系统地址"
                ) % len(api_check_details or api_checks)
                failure_reasons.append(msg)
                _log.error("[API直调] %s %s", cid, msg)
        if db_checks:
            from src.pipeline.steps.step4.db_verifier import verify_db

            # ★ 契约修复(2026-09-27): 按 check 是否携带已注册 type 分流——
            #   ① 有 type → 交 DB 轨权威注册器 DbTrackVerifier 执行（"按用例指令执行"）：
            #      [SKIP]/[ERROR]（环境不可达、能力缺口）**不计失败**（防假 Bug），
            #      只有真实否定判定才 all_ok=False；且 skipped 不再被算作"已通过"。
            #   ② 无 type（历史 {table,where}/字符串）→ 保持 verify_db 原路径。
            _typed_db = [c for c in db_checks if isinstance(c, dict) and c.get("type")]
            _legacy_db = [
                c for c in db_checks if not (isinstance(c, dict) and c.get("type"))
            ]
            if _typed_db:
                from src.pipeline.steps.step4.verification._track_protocol import (
                    DbTrackVerifier,
                    render_check_tag as _render_db_tag,
                )

                try:
                    _dbres = _run_coro_sync(
                        DbTrackVerifier().verify(
                            [c.get("check") or c for c in _typed_db], {}
                        )
                    )
                except Exception as _dbe:  # noqa: BLE001
                    _dbres = {"details": []}
                    _log.warning(
                        "[API直调] %s DB轨类型化校验异常(不计产品缺陷): %s",
                        cid, str(_dbe)[:120],
                    )
                for _d in _dbres.get("details") or []:
                    _tag = _render_db_tag(_d)
                    if _tag in ("[SKIP]", "[ERROR]"):
                        # 非判定态：既不记通过也不记失败，如实记录供报告
                        warnings.append("DB: %s" % str(_d.get("evidence", ""))[:80])
                        continue
                    _checks_total += 1
                    if _tag == "[PASS]":
                        _checks_passed += 1
                    else:
                        all_ok = False
                        msg = "DB校验失败: %s" % str(_d.get("evidence", ""))[:80]
                        failure_reasons.append(msg)
                        _log.error("[API直调] %s %s", cid, msg)
            for check in _legacy_db:
                r = verify_db(check)
                _checks_total += 1
                if r.get("success"):
                    _checks_passed += 1
                else:
                    all_ok = False
                    msg = "DB校验失败: %s" % str(check)[:60]
                    failure_reasons.append(msg)
                    _log.error("[API直调] %s %s", cid, msg)
                    _log.warning("  [API直调] %s %s", cid, msg)
        if not db_checks and not api_checks:
            all_ok = False
            msg = "api_only 测试用例没有 api_checks/db_checks，缺少可执行的客观校验点"
            failure_reasons.append(msg)
            _log.error("[API直调] %s %s", cid, msg)
            _log.warning("  [API直调] %s %s", cid, msg)

        status = "PASS" if all_ok else "FAIL"
        # ★ 置信度计算：按实际求值断言/DB 校验的通过比例映射到档位，
        #   与 step4/_confidence.py 同一理念；无求值项（all_ok=False）时落到最低档。
        _conf_ratio = (
            (_checks_passed / _checks_total) if _checks_total and _checks_total > 0 else 0.0
        )
        try:
            from src.core.pipeline_config import pipeline_config as _ppl_conf

            _all_pass = float(_ppl_conf.verification_confidence_all_pass)
            _has_pass = float(_ppl_conf.verification_confidence_has_pass)
            _has_fail = float(_ppl_conf.verification_confidence_has_fail)
            _all_fail = float(_ppl_conf.verification_confidence_all_fail)
        except Exception:
            _all_pass, _has_pass, _has_fail, _all_fail = 0.95, 0.65, 0.35, 0.10
        if _conf_ratio >= 1.0:
            _base = _all_pass
        elif _conf_ratio >= 0.67:
            _base = _has_pass
        elif _conf_ratio > 0:
            _base = _has_fail
        else:
            _base = _all_fail
        _conf_bonus = 0.03 if _checks_passed > 0 else 0.0  # 客观 HTTP/DB 证据奖励
        _confidence = round(min(1.0, _base + _conf_bonus), 3)
        combined_reason = (
            "; ".join(failure_reasons + warnings) if failure_reasons or warnings else ""
        )
        if all_ok:
            passed += 1
        else:
            failed += 1

        # ★ P0+P2 修复：API 校验失败 = 被测系统 bug（PRODUCT_BUG），不是引擎问题
        #   依据 failure_classifier.py 分类标准：API 返回码/字段不符 = 被测系统行为不符合契约
        # 区分前置条件缺失(认证未配置/401) 与 真正的产品缺陷
        if not all_ok and _auth_blocked:
            _failure_category = "ENV_AUTH_BLOCKED"
        elif not all_ok:
            _failure_category = "PRODUCT_BUG"
        else:
            _failure_category = ""
        # ★ P0 修复：构造 failure_chain JSON，记录 check 级别失败明细
        import json as _json

        _failure_chain = (
            _json.dumps(
                {
                    "executor": "api_only",
                    "api_checks": api_check_details,
                    "failure_reasons": failure_reasons,
                    "warnings": warnings,
                },
                ensure_ascii=False,
            )
            if not all_ok
            else ""
        )
        # ★ 根因修复（2026-09-23）D16 —— 软性失败被记为 PASS = 系统性假 PASS 生成路径：
        #   实测 TC-REQ-003-PF01（本轮 round4 实时抓到）：断言观测到 HTTP 404
        #   （details.observed_passed=false，observed_body 是 <title>404 Not Found</title>），
        #   但 _verify_single_assertion 对"HTTP<500 且响应体不含预期文本"返回
        #   severity='warning'（软性失败），调用方只 append 到 warnings 而不置
        #   all_ok=False → `status = "PASS" if all_ok else "FAIL"` 直接放行。
        #   后果：**一条客观断言都没真实通过**的用例被报成 PASS（假绿），
        #   直接违反项目铁律"禁止假 PASS"。
        #   修复原则（既不假绿也不凭空造 FAIL）：PASS 必须至少有一条客观断言
        #   真实通过；若求值过（_checks_total>0）却无一通过（_checks_passed==0）
        #   → 判 SKIP(SOFT_ASSERTION_UNVERIFIABLE)，如实标注"无可信判定依据"。
        #   通用性：只看"是否有客观断言真实通过"，与业务/系统/行业无关。
        _soft_unverified = False
        if status == "PASS" and _checks_passed == 0 and (api_checks or db_checks):
            # ★ 契约修复(2026-09-27): 条件由 `_checks_total>0` 放宽为"声明了校验点但零通过"，
            #   覆盖"全部校验点均为非判定态（[SKIP]/[ERROR]）"的情形——
            #   原条件下 _checks_total==0（全 skipped）时该守卫不触发 → 无证据却报 PASS。
            _soft_unverified = True
            status = "SKIP"
            _failure_category = "SOFT_ASSERTION_UNVERIFIABLE"
            _failure_chain = ""
            combined_reason = (
                "【SKIP】全部客观断言均无有效判定（全部跳过/无结论/引擎能力缺口），"
                "无可信通过依据 → 不记 PASS（防假绿），也不记 FAIL（防假 Bug）"
                + ("; " + combined_reason if combined_reason else "")
            )
            passed -= 1
            _log.warning(
                "[API直调] %s 零有效判定 → SKIP（防假PASS）: %s",
                cid, combined_reason[:160],
            )

        # ★ P0 修复：提取 expected/actual/details 用于失败可分析性
        # ★ 2026-08-30 证据完整性改进: expected=意图断言文本; actual=各端点实际观测 HTTP 状态;
        #   details=含 observed_* 的结构化全证据（可从 DB 单独复算 PASS/FAIL）。
        _expected = "; ".join(
            str(c.get("assertion", "")) or str(c)
            for c in (api_check_details if api_check_details else api_checks)
            if c
        )
        _actual = "; ".join(
            _a
            for c in (api_check_details or [])
            if isinstance(c, dict)
            for _a in [str(c.get("actual", "") or c.get("observed_status", "") or "").strip()]
            if _a
        )
        _details = (
            _json.dumps(api_check_details, ensure_ascii=False)
            if api_check_details
            else ""
        )

        # ★ 先写 MySQL executions（权威事实源）
        # ★ P0 修复：删除双重写入（原 467-474 行的 excel_handler.update_verification_status 第二次 INSERT）
        #   原因：update_verification_status 会再 INSERT 一行 failure_reason="" 的空记录，
        #   导致查询最新记录时返回全空记录，覆盖了第一次的完整记录。
        try:
            from src.users.manager import record_execution

            _case_asset_path = getattr(excel_handler, "_case_asset_path", None)
            case_asset_file = str(
                intent.get("case_asset_file", "")
                or (_case_asset_path.name if _case_asset_path is not None else "")
            )
            from src.backend.state import _thread_run_id

            _run_id = getattr(_thread_run_id, "value", "") or ""
            if case_version_id:
                from src.core.versioning import execution_version_context

                with execution_version_context(case_version_id):
                    record_execution(
                        user_id or "",
                        project_name,
                        cid,
                        status,
                        failure_reason=combined_reason,
                        case_asset_file=case_asset_file,
                        run_id=_run_id,
                        failure_category=_failure_category,
                        failure_chain=_failure_chain,
                        check_type="api_exec",
                        expected=_expected,
                        actual=_actual,
                        details=_details,
                        confidence=_confidence,
                    )
            else:
                record_execution(
                    user_id or "",
                    project_name,
                    cid,
                    status,
                    failure_reason=combined_reason,
                    case_asset_file=case_asset_file,
                    run_id=_run_id,
                    failure_category=_failure_category,
                    failure_chain=_failure_chain,
                    check_type="api_exec",
                    expected=_expected,
                    actual=_actual,
                    details=_details,
                    confidence=_confidence,
                )
        except Exception as e:
            _log.error("[API直调] %s 执行记录写入失败: %s", cid, str(e)[:80])
            _log.info("  [API直调] %s 执行记录写入失败: %s", cid, str(e)[:80])

        # ★ AI 健康度指标记录（api_only 路径此前缺失，导致 KPI 面板虚假 0%/100%）
        try:
            from src.extensions.metrics_collector import metrics_collector

            run_id = _run_id or ""
            if run_id:
                # 记录 api_only 动作尝试（无论成功/失败）
                metrics_collector.record_action_attempt(
                    project_name, run_id, "api_only"
                )
                # 有 api_checks 但无 base_url → 记录守卫拦截（环境未就绪）
                if api_check_details and not base_url:
                    metrics_collector.record_guard_rejection(
                        project_name,
                        run_id,
                        "api_only_no_base_url",
                        reason_category="api_only_no_base_url",
                    )
                # ★ 三轨校验写入：无条件记录（根因修复 2026-09-02）。
                #   此前仅在存在 db_checks 时才调用，且 api_pass 硬编码 False：
                #   纯 API 靶子(只有 api_checks 无 db_checks)一条 verification 指标都不写，
                #   前端执行详情三轨(ui/api/db)全显示「—」——API 轨本应有值。
                #   现改为始终写入：API-only 的整体执行状态 all_ok 即 API 轨结果，
                #   db 轨仅在存在 db_checks 时求值，ui 轨对 API-only 标记不适用(False)。
                all_db_ok = False
                if db_checks:
                    try:
                        from src.pipeline.steps.step4.db_verifier import (
                            verify_db as _vdb,
                        )

                        all_db_ok = all(
                            _vdb(str(check)).get("success", False)
                            for check in db_checks
                        )
                    except Exception as _e:
                        _log.debug(
                            "[api_only_executor] 非关键异常: %s", _e, exc_info=True
                        )
                        all_db_ok = False
                metrics_collector.record_verification(
                    project=project_name or "",
                    run_id=run_id,
                    step_desc="api_only:%s" % str(cid)[:40],
                    ui_pass=False,
                    api_pass=bool(all_ok and not _soft_unverified),
                    db_pass=all_db_ok,
                    case_id=str(cid) if cid else "",
                )
        except Exception as _me:
            # ★  根因修复：原 _log.debug 在生产日志级别下完全不可见，
            #   埋点异常被静默吞噬 → 「executions 有行、ai_health_metrics 零行」
            #   且无从排查（/ai-health 显示「—」却查不到原因）。提升为 warning+堆栈。
            _log.warning(
                "[API直调] 健康度指标记录异常(用例=%s): %s",
                str(cid)[:40],
                str(_me)[:200],
                exc_info=True,
            )

        # ★ v7: 证据引擎校准回写（API-only 路径）
        with guard("execute_api_only_intents: from src.core.pipeline_config import", log=_log):
            from src.core.pipeline_config import pipeline_config as _ppl_cfg

            if getattr(_ppl_cfg, "evidence_engine_enabled", True):
                from src.pipeline.evidence.calibration import (
                    record_calibration as _record_cal,
                )

                _cal_entries = []
                _consensus = str(intent.get("_consensus_level", "") or "").strip()
                if _consensus:
                    _cal_entries.append(
                        {
                            "key": "consensus_level",
                            "value": _consensus,
                            "correct": all_ok,
                        }
                    )
                _pc = intent.get("_perspective_count", 0)
                if _pc and int(_pc) > 0:
                    _pc_val = "1" if int(_pc) == 1 else ("2" if int(_pc) == 2 else "3+")
                    _cal_entries.append(
                        {
                            "key": "perspective_count",
                            "value": _pc_val,
                            "correct": all_ok,
                        }
                    )
                _sq = str(intent.get("source_quote", "") or "").strip()
                _cal_entries.append(
                    {
                        "key": "source_quote",
                        "value": "yes" if _sq else "no",
                        "correct": all_ok,
                    }
                )
                _risk = str(intent.get("risk_level", "") or "").strip()
                if _risk:
                    _cal_entries.append(
                        {"key": "risk_level", "value": _risk, "correct": all_ok}
                    )
                _itype = str(
                    intent.get("case_type", "") or intent.get("type", "") or ""
                ).strip()
                if _itype:
                    _cal_entries.append(
                        {"key": "case_type", "value": _itype, "correct": all_ok}
                    )
                _rtype = str(intent.get("requirement_type", "") or "").strip()
                if _rtype:
                    _cal_entries.append(
                        {"key": "requirement_type", "value": _rtype, "correct": all_ok}
                    )
                if _cal_entries:
                    _record_cal(project_name, _cal_entries)

    _log.info("[API直调] 完成: pass=%d fail=%d", passed, failed)
    return {"passed": passed, "failed": failed, "total": len(intents)}
