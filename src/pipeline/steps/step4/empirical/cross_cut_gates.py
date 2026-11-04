# -*- coding: utf-8 -*-
"""横切门禁统一入口 — a11y / perf / visual 三横切维度的单一执行源。

背景（2026-09-02）：三个横切门禁原先只挂在 ``target_empirical``（DeepAssertionRunner）
单一后端，医院等走 Agent/标准步循环后端的用例不跑这三项，造成"同一靶子换后端横切维度
缺失"。本模块把三入门禁收敛为**一个共享 helper**，任意执行后端都能以相同方式挂载；
每个门禁独立 fail-safe（任何异常只记录，不阻断用例 PASS/FAIL，不伪造结论）。

通用性：
  - 不绑定靶子系统/页面：各门禁默认值即为通用配置（a11y 默认开、visual 无 expectation
    时诚实 NO_DATA、perf 用默认基线），target_cfg 可选覆盖。
  - 一处定义、任意后端复用，避免三端各自粘贴调用代码。
"""
from __future__ import annotations
from src.core.guarded import guard

import inspect
import logging
from typing import Any

_log = logging.getLogger(__name__)


def run_a11y_check(page: Any, target_cfg: dict | None = None) -> dict[str, Any]:
    """运行可访问性门禁（8 条可解释规则，纯 DOM、零外部依赖）。fail-safe。"""
    try:
        from src.pipeline.steps.step4.empirical.a11y_checker import (
            run_a11y_check as _run,
        )
        return _run(page, target_cfg or {}) or {}
    except Exception as e:
        _log.warning("[cross-cut] a11y 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "error": "a11y 门禁异常: %s" % str(e)[:100],
                "findings": [], "total": 0, "by_severity": {}}


async def run_a11y_check_async(page: Any, target_cfg: dict | None = None) -> dict[str, Any]:
    """异步执行路径的 a11y 门禁包装（fail-safe 契约与同步版一致）。

    配套根因修复(2026-09-21)：异步栈（PlaywrightEngine）上同步版永远拿不到
    HTML（page.content() 返回协程），详见 a11y_checker.run_a11y_check_async。
    """
    try:
        from src.pipeline.steps.step4.empirical.a11y_checker import (
            run_a11y_check_async as _run,
        )
        return await _run(page, target_cfg or {}) or {}
    except Exception as e:
        _log.warning("[cross-cut] a11y 门禁(异步)异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "error": "a11y 门禁异常: %s" % str(e)[:100],
                "findings": [], "total": 0, "by_severity": {}}


# 用例集指令名 run_a11y_gate 的异步别名（保持与同步命名对称）
run_a11y_gate_async = run_a11y_check_async


def run_perf_check(page: Any, api_calls: Any, target_cfg: dict | None = None,
                   run_id: str = "", case_id: str = "",
                   report_dir: str | None = None) -> dict[str, Any]:
    """运行性能门禁（采集→门禁→回归对比）。fail-safe。"""
    try:
        from src.pipeline.steps.step4.empirical.perf_checker import (
            run_full_perf_check as _run,
        )
        return _run(page, api_calls, target_cfg or {}, run_id=run_id,
                    case_id=case_id, regression_report_dir=report_dir) or {}
    except Exception as e:
        _log.warning("[cross-cut] perf 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "error": "perf 门禁异常: %s" % str(e)[:100],
                "samples": 0, "verdicts": [], "summary": {}}


def run_visual_gate(page: Any, target_cfg: dict | None = None) -> dict[str, Any]:
    """运行视觉门禁（云端→本地VLM→LocalBackend→诚实 NO_DATA）。fail-safe。"""
    try:
        from src.pipeline.steps.step4.empirical.visual_checker import (
            run_visual_gate as _run,
        )
        return _run(page, target_cfg or {}) or {}
    except Exception as e:
        _log.warning("[cross-cut] visual 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "status": "NO_DATA", "passed": None,
                "skipped": True, "confidence": 0.0,
                "error": "visual 门禁异常: %s" % str(e)[:100]}


def run_security_gate(page: Any, target_cfg: dict | None = None) -> dict[str, Any]:
    """运行安全门禁（OWASP 规则驱动：SQLi/XSS/敏感信息/安全头/CSRF/路径遍历/命令注入/IDOR）。

    补齐核心目标「覆盖全面…安全…」的一等公民横切维度，与 a11y/perf/visual 对齐。
    对页面 HTML 跑 SecurityScanner，产出 findings 与 severity。fail-safe。
    """
    try:
        from src.pipeline.steps.step4.empirical.security_checker import (
            run_security_check,
        )
        return run_security_check(page, target_cfg or {}) or {}
    except Exception as e:
        _log.warning("[cross-cut] security 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "source": "step4", "total": 0,
                "by_severity": {}, "findings": [],
                "error": "security 门禁异常: %s" % str(e)[:100]}


def run_authz_gate(target_cfg: dict | None = None) -> dict[str, Any]:
    """运行权限矩阵（越权/访问控制）横切门禁 — 复用 AuthzMatrixVerifier。

    ★ 补齐缺口（2026-09-05）：authz_mat sinus 原先只挂在 target_empirical 的**独立
      authz 模式**（`target_cfg["authz"]` 时单独 run），未进横切门禁。本门禁把
      RBAC 越权验证提升为首等横切安全维度：任何注册靶子只要在 `target_cfg.authz`
      声明 permission_matrix + probes（登录凭证来自 `target_cfg.auth`），横切门禁即以
      run 级一次执行全部权限探针（allow/deny/allow_self_only/deny_but_suspect、
      覆盖正向/负向/边界/默认拒绝/水平越权/垂直越权）。

    通用性：不绑靶子业务——输入全由 target_cfg.authz 驱动，本门禁只负责按探针发
    requests 并比对期望（对齐软件测试理论之访问控制测试）。纯 API（requests），
    不依赖 page；无 authz 配置时诚实返回空（不伪造）。

    fail-safe：异常仅在 ``security.authz.error`` 标注，不阻断用例判定。

    Returns:
        dict: {enabled, all_pass, total, passed, failed, probes, error}。
    """
    cfg = (target_cfg or {}).get("authz") or {}
    # 无 authz 配置 → 非 RBAC 靶子，诚实跳过（enabled=True 但 total=0，不伪报）
    if not cfg.get("permission_matrix") or not cfg.get("probes"):
        _log.debug("[cross-cut] 靶子未声明 authz 配置，authz 门禁跳过(不伪报)")
        return {"enabled": True, "all_pass": None, "total": 0, "passed": 0,
                "failed": 0, "probes": [], "error": ""}
    try:
        from src.pipeline.steps.step4.empirical.authz_matrix import (
            AuthzMatrixVerifier,
        )
        report = AuthzMatrixVerifier(target_cfg=target_cfg or {}).run()
        s = report.get("summary") or {}
        return {
            "enabled": True,
            "all_pass": bool(s.get("all_pass")),
            "total": int(s.get("total", 0)),
            "passed": int(s.get("passed", 0)),
            "failed": int(s.get("failed", 0)),
            "probes": report.get("probes") or [],
            "error": "",
        }
    except Exception as e:
        _log.warning("[cross-cut] authz 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "all_pass": None, "total": 0, "passed": 0,
                "failed": 0, "probes": [],
                "error": "authz 门禁异常: %s" % str(e)[:100]}


def _inject_security_headers(
    target_cfg: dict | None, headers: dict[str, str]
) -> dict | None:
    """把页面顶层文档响应头注入 target_cfg.security.headers（HEAD-001 判真用）。

    无 headers 或 target_cfg 为 None 时原样返回；显式已配置的 headers 不覆盖。
    """
    if not headers or not target_cfg:
        return target_cfg
    sec = dict(target_cfg.get("security") or {})
    if sec.get("headers"):
        return target_cfg
    sec["headers"] = dict(headers)
    return {**target_cfg, "security": sec}


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    """★ 根因修复(2026-09-15)：Playwright/httpx 返回的响应头键名是全小写
    （如 ``x-frame-options``），但 SecurityScanner.SECURITY_HEADERS 用首字母大写
    精确匹配（如 ``X-Frame-Options``）。HTTP header 规范不区分大小写，这里
    标准化为首字母大写（每个 ``-`` 段首字母大写），保证 scanner 能正确命中。
    """
    return {
        "-".join(p.capitalize() for p in k.split("-")): v
        for k, v in headers.items()
    }


def _fetch_doc_headers_sync(page: Any) -> dict[str, str]:
    """同步 Page 探测页面顶层文档响应头（无 request 通道时返回空，调用方自动跳过）。"""
    req = getattr(page, "request", None)
    if req is None:
        return {}
    url = str(getattr(page, "url", "") or "")
    if not url:
        return {}
    try:
        resp = req.get(url, timeout=5000)
        return _normalize_headers(dict(resp.headers)) if resp is not None else {}
    except Exception as _e:
        _log.debug("[cross-cut] 同步抓文档响应头失败(跳过 HEAD-001): %s", str(_e)[:80])
        return {}


async def _fetch_doc_headers_async(page: Any) -> dict[str, str]:
    """async Page 探测页面顶层文档响应头（同上，await 变体）。"""
    req = getattr(page, "request", None)
    if req is None:
        return {}
    url = str(getattr(page, "url", "") or "")
    if not url:
        return {}
    try:
        resp = await req.get(url, timeout=5000)
        return _normalize_headers(dict(resp.headers)) if resp is not None else {}
    except Exception as _e:
        _log.debug("[cross-cut] 异步抓文档响应头失败(跳过 HEAD-001): %s", str(_e)[:80])
        return {}


def run_cross_cut_gates(page: Any, api_calls: Any, target_cfg: dict | None = None,
                        run_id: str = "", case_id: str = "",
                        report_dir: str | None = None) -> dict[str, Any]:
    """横切门禁统一执行入口。

    Args:
        page: Playwright 同步 Page（已导航；可为 None，相应门禁内部处理）。
        api_calls: ApiTracker 调用记录（API perf 用；None 表示 browser-only）。
        target_cfg: 靶子配置（含可选的 a11y/perf/visual/security 段；None 用各门禁默认）。
        run_id / case_id: 归属信息。
        report_dir: 性能回归对比历史报告目录（None 则仅基线采集）。

    Returns:
        {"a11y": {...}, "perf": {...}, "visual": {...}, "security": {...}} —— 每个值
        独立维度，fail-closed，绝不伪造结论，不影响用例 PASS/FAIL。
    """
    # 【G3】browser 轨 HEAD-001 判真：探测页面顶层文档响应头注入 security.headers
    target_cfg = _inject_security_headers(target_cfg, _fetch_doc_headers_sync(page))
    return {
        "a11y": run_a11y_check(page, target_cfg),
        "perf": run_perf_check(page, api_calls, target_cfg, run_id=run_id,
                               case_id=case_id, report_dir=report_dir),
        "visual": run_visual_gate(page, target_cfg),
        "security": _run_security_bundle(page, target_cfg),
    }


class _StaticPage:
    """同步 Page 的静态替身：用提前抓好的 html / screenshot 喂给横切门禁。

    背景：step4 常规流水线（serial._execute_all）走 **async** PlaywrightEngine，
    而 a11y / visual 检定器设计为同步 ``page.content()`` / ``page.screenshot()``。
    本 shim 把 async Page 早已抓好的 DOM 与截图再一次暴露成同步接口，
    使三入门禁在 async 后端上也能以**完全相同**的方式复用，不复制任何门禁逻辑。
    仅实现门禁用到的两个方法；其余属性（url 等）门禁内部均以默认值容错。
    """

    no_screenshot = object()

    def __init__(self, html: str = "", screenshot: object = no_screenshot) -> None:
        self._html = html or ""
        self._shot = screenshot

    def content(self) -> str:
        return self._html

    def screenshot(self, full_page: bool = False):  # noqa: F841 — 同步门禁签名兼容
        if self._shot is _StaticPage.no_screenshot:
            raise RuntimeError("静态快照未含截图（async 抓取失败），视觉门禁应判 NO_DATA")
        return self._shot

    @property
    def url(self) -> str:
        return ""


async def _fetch_page_snapshot(page: Any) -> tuple[str, object]:
    """从 **async** Page 一次性抓取 html + 截图（各自独立 fail-open，不互相影响）。"""
    html = ""
    shot: object = _StaticPage.no_screenshot
    if page is None:
        return html, shot
    for _attr in ("content", "screenshot"):
        with guard("_fetch_page_snapshot: _res = getattr(page, _attr)()", log=_log, level=logging.WARNING):
            _res = getattr(page, _attr)()
            if inspect.isawaitable(_res):
                _res = await _res
            if _res is None:
                continue
            if _attr == "content":
                html = _res
            else:
                shot = _res
    return html, shot


async def run_perf_check_async(page: Any, api_calls: Any, target_cfg: dict | None = None,
                               run_id: str = "", case_id: str = "",
                               report_dir: str | None = None) -> dict[str, Any]:
    """性能门禁 async 版本（Web 采集 await evaluate + API/门禁/回归与同步共用）。"""
    try:
        from src.pipeline.steps.step4.empirical.perf_checker import (
            run_full_perf_check_async as _run,
        )
        return await _run(page, api_calls, target_cfg or {}, run_id=run_id,
                          case_id=case_id, regression_report_dir=report_dir) or {}
    except Exception as e:
        _log.warning("[cross-cut] perf(async) 门禁异常(不阻断): %s", str(e)[:120])
        return {"enabled": True, "error": "perf(async) 门禁异常: %s" % str(e)[:100],
                "samples": 0, "verdicts": [], "summary": {}}


async def run_empirical_usability_gate(page: Any, target_cfg: dict | None = None) -> dict[str, Any]:
    """易用性 + 兼容性 **运行时** 门禁（2026-09-16 B 方案新增）。

    与 a11y（静态 HTML 规则）不同，本 gate 用 **活 Page.evaluate** 执行 6 个
    真实 check——需要真实 viewport、真实布局、真实元素尺寸。跑在 **async**
    cross_cut_gates 里（只有 async 后端能拿到活 Page）。

    6 个 check（★2026-09-26：第 6 项仅在声明支持移动端时执行，默认 5 项）:
      - click_target_size_check (易用, WCAG 2.5.5, min 32px)
      - form_label_association_check (易用, WCAG 3.3.2)
      - viewport_overflow_check (易用, 响应式)
      - form_autocomplete_check (易用, WCAG 1.3.5)
      - viewport_rendering_check (兼容, **声明支持矩阵**循环，不再硬编码 375/768/1920)
      - touch_target_mobile_check (兼容, Apple HIG min 44px；仅当矩阵含移动端)

    target_cfg.usability 可覆盖阈值:
      {"min_click": 32, "min_touch": 44, "viewports": [...]}

    异常 fail-safe，不阻断用例判定。
    """
    cfg = (target_cfg or {}).get("usability") or {}
    min_click = int(cfg.get("min_click", 32))
    min_touch = int(cfg.get("min_touch", 44))
    # ★  根因修复：默认 viewport 矩阵改取**声明支持矩阵**，不再硬编码
    #   375/768/1920。根因：靶子未声明移动端时硬测 375px 必然横向溢出 → 门禁假 FAIL
    #   （引擎自造需求）。单一事实源 = src/pipeline/step3/_fallback_cases
    #   .declared_support_viewports()（config 可用 support_viewports 覆盖）。
    try:
        from src.pipeline.step3._fallback_cases import (
            declared_support_viewports as _declared_vps,
            matrix_supports_mobile as _supports_mobile,
        )

        _default_vps = _declared_vps()
    except Exception as _e:  # noqa: BLE001 — 读取失败回落桌面默认
        _log.debug("[usability] 声明矩阵读取失败，回落桌面默认: %s", str(_e)[:80])
        _default_vps = [
            {"w": 1366, "h": 768, "label": "desktop"},
            {"w": 1920, "h": 1080, "label": "desktop-fhd"},
        ]

        def _supports_mobile(_vps=None):
            return any(int(v.get("w") or 0) < 768 for v in (_vps or _default_vps))

    viewports = cfg.get("viewports") or _default_vps
    _n_checks = 5 + (1 if _supports_mobile(viewports) else 0)

    if page is None:
        return {
            "enabled": True, "total": _n_checks, "passed": 0, "failed": 0,
            "findings": [], "error": "page is None",
            "checks": [],
        }

    # 懒 import（避免循环依赖：verification._db_ui 反向引 empirical）
    try:
        from src.pipeline.steps.step4.verification._db_ui import (
            _handle_click_target_size_check,
            _handle_form_label_association_check,
            _handle_viewport_overflow_check,
            _handle_form_autocomplete_check,
            _handle_viewport_rendering_check,
            _handle_touch_target_mobile_check,
        )
    except Exception as exc:
        _log.error("[usability] import handler 失败: %s", exc)
        return {
            "enabled": True, "total": _n_checks, "passed": 0, "failed": 0,
            "findings": [], "error": f"import failed: {str(exc)[:160]}",
            "checks": [],
        }

    handlers = [
        ("click_target_size_check", _handle_click_target_size_check, {"min_size": min_click}),
        ("form_label_association_check", _handle_form_label_association_check, {}),
        ("viewport_overflow_check", _handle_viewport_overflow_check, {}),
        ("form_autocomplete_check", _handle_form_autocomplete_check, {}),
        ("viewport_rendering_check", _handle_viewport_rendering_check, {"viewports": viewports}),
    ]
    # ★ 2026-09-26：触控目标仅在**声明支持移动端**时纳入（否则 = 引擎自造需求）。
    if _supports_mobile(viewports):
        handlers.append(
            ("touch_target_mobile_check", _handle_touch_target_mobile_check, {"min_size": min_touch})
        )

    results = []
    findings = []
    for ctype, handler, overrides in handlers:
        try:
            res = await handler(page, {}, overrides, ctype, "fuzzy")
            results.append(res)
            if not res.get("passed", True):
                findings.append({
                    "type": ctype,
                    "passed": False,
                    "evidence": res.get("evidence", "")[:200],
                    "severity": "high" if ctype in ("click_target_size_check", "touch_target_mobile_check") else "medium",
                })
        except Exception as exc:
            _log.warning("[usability] %s 异常: %s", ctype, str(exc)[:100])
            results.append({"type": ctype, "passed": False, "evidence": f"异常: {str(exc)[:120]}"})
            findings.append({"type": ctype, "passed": False, "evidence": str(exc)[:200], "severity": "error"})

    passed = sum(1 for r in results if r.get("passed"))
    failed = len(results) - passed

    return {
        "enabled": True,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "findings": findings,
        "error": "",
        "checks": results,
        "summary": f"{passed}/{len(results)} PASS" if passed else "ALL FAIL",
    }


async def run_cross_cut_gates_async(page: Any, api_calls: Any,
                                    target_cfg: dict | None = None,
                                    run_id: str = "", case_id: str = "",
                                    report_dir: str | None = None) -> dict[str, Any]:
    """横切门禁统一执行入口 — **async** Page（常规流水线/串行后端挂载用）。

    与同步 :func:`run_cross_cut_gates` 结果 schema 一致；a11y/visual 走静态快照
    （提前一次性抓 html+screenshot），perf 走 async 采集。三入门禁独立 fail-safe，
    不阻断用例 PASS/FAIL，不伪造结论。

    ★ 2026-09-16 新增 empirical_usability 维度——async 版独有（需要活 Page）。
    """
    html, shot = await _fetch_page_snapshot(page)
    snapshot = _StaticPage(html, shot)
    # 【G3】browser 轨 HEAD-001 判真：async Page 探测顶层文档响应头注入 security.headers
    target_cfg = _inject_security_headers(target_cfg, await _fetch_doc_headers_async(page))
    return {
        "a11y": run_a11y_check(snapshot, target_cfg),
        "perf": await run_perf_check_async(page, api_calls, target_cfg,
                                           run_id=run_id, case_id=case_id,
                                           report_dir=report_dir),
        "visual": run_visual_gate(snapshot, target_cfg),
        "security": _run_security_bundle(snapshot, target_cfg),
        # ★ 2026-09-16 B 方案：易用/兼容运行时门禁
        "usability": await run_empirical_usability_gate(page, target_cfg),
    }


def _run_security_bundle(page: Any, target_cfg: dict | None) -> dict[str, Any]:
    """安全门禁聚合：OWASP DOM 扫描（run_security_gate）+ 越权/访问控制（run_authz_gate）。

    把「覆盖全面…安全…」拆为两个互补子维度，放入 ``security`` 键：
      - ``findings`` / ``total`` / ``by_severity``：OWASP 规则（DOM 扫描，对应
        SQLi/XSS/敏感信息/安全头/CSRF/路径遍历/命令注入/IDOR）。
      - ``authz``：RBAC 越权/访问控制（纯 API 探针，任何声明 ``target_cfg.authz``
        的靶子自动跑；无配置诚实跳过）。

    authz 不依赖 page（纯 requests），故此处可安全传 page=None；两子维度独立
    fail-safe，任一异常只记到对应字段，不阻断用例判定。
    """
    sec = run_security_gate(page, target_cfg)
    # authz 需要可用的 base_url（纯 API），横切门禁 page 为页面对象时 target_cfg 已含
    # base_url/url；直接复用 target_cfg 原样传给 AuthzMatrixVerifier。
    authz = run_authz_gate(target_cfg)
    return {**sec, "authz": authz}


def summarize_cross_cut(result: dict[str, Any]) -> str:
    """把横切门禁结果压缩为一行日志摘要（仅诊断，不改判定）。"""
    parts = []
    for _k in ("a11y", "perf", "visual", "security", "usability"):
        _r = (result or {}).get(_k) or {}
        _vis = "disabled" if not _r.get("enabled") else str(_r.get("status") or _r.get("summary") or "OK")
        parts.append("%s=%s" % (_k, _vis))
    # authz 子维度单独展示（有探针时）
    _authz = ((result or {}).get("security") or {}).get("authz") or {}
    if _authz.get("total"):
        parts.append("authz=%s/%s" % (_authz.get("passed", 0), _authz.get("total", 0)))
    return "横切门禁: " + "; ".join(parts)