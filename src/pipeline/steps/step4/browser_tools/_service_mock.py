# -*- coding: utf-8 -*-
"""外部依赖 mock / 桩（B5，CAPABILITY_GAP_PRD §3.2）—— 轻量 route 拦截 / 请求改写层。

基于 Playwright ``page.route``，零新依赖。

与 ``_network_fault.py`` 的分工（严禁混淆，两者互不干扰）：
  - ``_network_fault.py`` = **故障注入**（F9）：制造异常
    （offline/abort/throttle/connection_reset），目的是「让它坏」。
  - ``_service_mock.py``  = **可控打桩**（B5）：让第三方 API / 支付网关 / 邮件服务
    返回「我指定的结果」，目的是「让它按我说的返」——外部依赖不可用时不阻塞测试。

args["mode"]：
  register → 注册 mock 规则（``rules`` 数组，或单规则平铺）
  clear    → 清除本 page 上所有 mock 规则并 unroute
  stats    → 读取命中统计（供断言消费；不改变任何状态）

规则字段（rule dict）：
  url_pattern 必填 URL 匹配（子串 / ``*`` 通配 / ``^`` 正则，复用 _url_match 语义）
  methods     选填 ``["GET","POST"]``；空 = 不限方法
  action      选填 ``fulfill``(默认) | ``rewrite`` | ``passthrough``
  status      选填 fulfill 的 HTTP 状态码（默认 200）
  body        选填 fulfill 静态响应体（str 原样；dict/list → JSON）
  template    选填 fulfill 规则生成响应体（占位符见 ``_render_template``）
  headers     选填 fulfill 追加响应头（dict）
  delay_ms    选填 响应前可控延迟（毫秒，非故障注入）
  times       选填 最大生效次数（默认 0 = 不限）；次数用尽后放行真实请求
  rewrite     选填 action=rewrite 的改写体
              ``{"url":..,"method":..,"headers":{..},"post_data":..}``

★ 安全铁律：仅当 ``SERVICE_MOCK_ENABLED`` 且用例显式声明 ``service_mock`` 动作才生效；
  默认 False → ``apply_service_mock`` 明确拒绝（不允许静默打桩，避免假绿）。
★ 用后必恢复：``recover_service_mock`` 显式 unroute 清理并清空规则表。

★ B5 关键改动（2026-08-16）[未复核-需 GLM/DeepSeek 把关]：
  新增 route 拦截 / 请求改写层 + 命中统计；SERVICE_MOCK_ENABLED 门控，默认 OFF 零行为变更。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import random
import time
import uuid
from typing import Any

_log = logging.getLogger(__name__)

# 单规则平铺时允许直接出现在 args 顶层的字段（便于用例简写）
_RULE_FIELDS = (
    "url_pattern",
    "methods",
    "method",
    "action",
    "status",
    "body",
    "template",
    "headers",
    "delay_ms",
    "times",
    "rewrite",
)

_VALID_ACTIONS = ("fulfill", "rewrite", "passthrough")
_VALID_MODES = ("register", "clear", "stats")

# page 上挂载的 mock 状态属性名（与 _network_fault 的 _network_fault_routes 隔离）
_STATE_ATTR = "_service_mock_state"


class ToolExecutionResult:
    """与 browser_tools 一致的执行结果结构（避免循环依赖）。"""

    def __init__(self, ok: bool, message: str = "", data: Any = None):
        self.ok = ok
        self.message = str(message or "")
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "data": self.data}


def _enabled() -> bool:
    """SERVICE_MOCK_ENABLED 门控（默认 False → 零行为变更）。"""
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        return bool(_cfg("SERVICE_MOCK_ENABLED", False))
    except Exception:  # noqa: BLE001
        return False


def _get_page(engine) -> Any:
    """从 engine 取 page（兼容 BrowserCore engine.page / engine._page）。"""
    if engine is None:
        return None
    return getattr(engine, "page", None) or getattr(engine, "_page", None)


def _route_glob(url_pattern: str) -> str:
    """url_pattern → Playwright route glob（收窄拦截面，避免 catch-all 拖慢页面）。

    子串 / 正则 / 含 ``*`` 的复杂模式统一退化为 ``**/*``（宽 glob 先接住，
    再由 handler 内 ``_rule_matches`` 做精确判定），保证语义不丢。
    """
    p = str(url_pattern or "").strip()
    if not p:
        return "**/*"
    if p.startswith("^"):  # 正则 → 宽 glob 兜底 + handler 精判
        return "**/*"
    if "*" in p:
        return p if p.startswith(("http://", "https://", "**")) else f"**{p}"
    if p.startswith(("http://", "https://")):
        return f"{p}*"
    return f"**{p}*"


def _url_matches(url_pattern: str, actual_url: str) -> bool:
    """复用 verification._url_match 的三态语义（精确包含 / ``*`` 通配 / ``^`` 正则）。"""
    if not url_pattern:
        return True
    try:
        from src.pipeline.steps.step4.verification._url_match import (
            _match_url_pattern,
        )

        return bool(_match_url_pattern(str(url_pattern), str(actual_url or "")))
    except Exception:  # noqa: BLE001
        return str(url_pattern).lower() in str(actual_url or "").lower()


def _normalize_rule(raw: dict) -> dict[str, Any]:
    """规则归一化 + 默认值填充（非法 action → fulfill）。"""
    rule: dict[str, Any] = dict(raw or {})
    action = str(rule.get("action", "") or "fulfill").strip().lower()
    if action not in _VALID_ACTIONS:
        action = "fulfill"
    methods_raw = rule.get("methods")
    if not methods_raw and rule.get("method"):
        methods_raw = [rule.get("method")]
    methods = [
        str(m).strip().upper() for m in (methods_raw or []) if str(m or "").strip()
    ]
    headers = rule.get("headers")
    return {
        "url_pattern": str(rule.get("url_pattern", "") or "").strip(),
        "methods": methods,
        "action": action,
        "status": _safe_int(rule.get("status"), 200),
        "body": rule.get("body"),
        "template": rule.get("template"),
        "headers": dict(headers) if isinstance(headers, dict) else {},
        "delay_ms": max(_safe_int(rule.get("delay_ms"), 0), 0),
        "times": max(_safe_int(rule.get("times"), 0), 0),
        "rewrite": rule.get("rewrite") if isinstance(rule.get("rewrite"), dict) else {},
        # 运行期统计
        "hits": 0,
        "last_url": "",
        "last_method": "",
    }


def _safe_int(val: Any, default: int) -> int:
    """安全整型转换（None/非法 → default）。"""
    try:
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _get_state(page) -> dict[str, Any]:
    """取（或初始化）page 上的 mock 状态：{rules, patterns, seq, rng}。"""
    state = getattr(page, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {"rules": [], "patterns": [], "seq": 0, "seed": None}
        setattr(page, _STATE_ATTR, state)
    return state


def _rule_matches(rule: dict, method: str, url: str) -> bool:
    """规则是否命中本次请求（方法 + URL + 剩余次数）。"""
    times = _safe_int(rule.get("times"), 0)
    if times > 0 and _safe_int(rule.get("hits"), 0) >= times:
        return False  # 次数用尽 → 放行真实请求
    methods = rule.get("methods") or []
    if methods and str(method or "").upper() not in methods:
        return False
    return _url_matches(str(rule.get("url_pattern", "") or ""), url)


def _render_template(template: Any, ctx: dict[str, Any]) -> str:
    """规则生成响应体：替换模板占位符。

    支持占位符（str 模板与 dict/list 模板均生效；dict/list 先 JSON 序列化再替换）：
      ``{{seq}}``        本 page 内自增序号（1 起）
      ``{{timestamp}}``  epoch 秒
      ``{{iso_time}}``   ISO8601（UTC）
      ``{{uuid}}``       uuid4 hex
      ``{{rand}}``       0-999999 随机整数（seed 可复现）
      ``{{req.method}}`` 本次请求方法
      ``{{req.url}}``    本次请求 URL
      ``{{req.query.K}}``  本次请求 query 参数 K
      ``{{req.json.K}}``   本次请求 JSON body 顶层字段 K

    Args:
        template: str / dict / list 模板。
        ctx: 渲染上下文（seq/rng/method/url/query/json）。

    Returns:
        渲染后的字符串（dict/list 模板返回 JSON 文本）。
    """
    if isinstance(template, (dict, list)):
        text = json.dumps(template, ensure_ascii=False)
    else:
        text = str(template or "")
    if "{{" not in text:
        return text

    rng: random.Random = ctx.get("rng") or random
    repl: dict[str, str] = {
        "{{seq}}": str(ctx.get("seq", 0)),
        "{{timestamp}}": str(int(time.time())),
        "{{iso_time}}": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "{{uuid}}": uuid.uuid4().hex,
        "{{rand}}": str(rng.randint(0, 999999)),
        "{{req.method}}": str(ctx.get("method", "") or ""),
        "{{req.url}}": str(ctx.get("url", "") or ""),
    }
    for token, value in repl.items():
        text = text.replace(token, value)

    # {{req.query.K}} / {{req.json.K}} 动态键
    for prefix, source in (
        ("{{req.query.", ctx.get("query") or {}),
        ("{{req.json.", ctx.get("json") or {}),
    ):
        while prefix in text:
            start = text.index(prefix)
            end = text.find("}}", start)
            if end < 0:
                break
            key = text[start + len(prefix) : end]
            val = source.get(key, "") if isinstance(source, dict) else ""
            text = text[:start] + str(val if val is not None else "") + text[end + 2 :]
    return text


def _parse_query(url: str) -> dict[str, str]:
    """解析 URL query（失败 → {}）。"""
    try:
        from urllib.parse import parse_qsl, urlsplit

        return {
            str(k): str(v)
            for k, v in parse_qsl(urlsplit(str(url or "")).query, keep_blank_values=True)
        }
    except Exception:  # noqa: BLE001
        return {}


def _parse_json_body(raw: Any) -> dict[str, Any]:
    """解析请求 JSON body 顶层 dict（失败 → {}）。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def _read_request(route) -> tuple[str, str, Any]:
    """从 route 读取 (method, url, post_data)；任何字段缺失容错为空。"""
    req = getattr(route, "request", None)
    method, url, post_data = "", "", None
    with guard("_read_request: method = str(getattr(req, 'method', ", log=_log):
        method = str(getattr(req, "method", "") or "")
        url = str(getattr(req, "url", "") or "")
        post_data = getattr(req, "post_data", None)
    return method, url, post_data


async def _pass_through(route) -> None:
    """规则不适用 → 放行（优先 fallback 让其它 handler 有机会，兜底 continue_）。"""
    with guard("_pass_through: fallback = getattr(route, 'fallback'", log=_log):
        fallback = getattr(route, "fallback", None)
        if callable(fallback):
            await fallback()
            return
    with guard("_pass_through: await route.continue_()", log=_log):
        await route.continue_()


async def _apply_delay(page, delay_ms: int) -> None:
    """可控延迟（复用 page.wait_for_timeout，无第三方依赖）。"""
    if delay_ms <= 0:
        return
    with guard("_apply_delay: await page.wait_for_timeout(delay_ms", log=_log):
        await page.wait_for_timeout(delay_ms)


async def _do_fulfill(route, rule: dict, ctx: dict[str, Any]) -> None:
    """返回 mock 响应（静态 body 或基于规则模板生成）。"""
    if rule.get("template") is not None:
        body_text = _render_template(rule.get("template"), ctx)
        content_type = "application/json"
    else:
        raw = rule.get("body")
        if isinstance(raw, (dict, list)):
            body_text = json.dumps(raw, ensure_ascii=False)
            content_type = "application/json"
        elif raw is None:
            body_text = "{}"
            content_type = "application/json"
        else:
            body_text = str(raw)
            content_type = "application/json" if body_text.strip().startswith(("{", "[")) else "text/plain"

    headers = {k: str(v) for k, v in (rule.get("headers") or {}).items()}
    kwargs: dict[str, Any] = {
        "status": _safe_int(rule.get("status"), 200),
        "body": body_text,
        "content_type": content_type,
    }
    if headers:
        kwargs["headers"] = headers
    await route.fulfill(**kwargs)


async def _do_rewrite(route, rule: dict, method: str, url: str, post_data: Any) -> None:
    """改写请求后继续（URL / method / headers / post_data 可选覆盖）。"""
    rw = rule.get("rewrite") or {}
    kwargs: dict[str, Any] = {}
    if rw.get("url"):
        kwargs["url"] = str(rw["url"])
    if rw.get("method"):
        kwargs["method"] = str(rw["method"]).upper()
    if isinstance(rw.get("headers"), dict) and rw["headers"]:
        merged: dict[str, str] = {}
        with guard("_do_rewrite: req = getattr(route, 'request', None", log=_log):
            req = getattr(route, "request", None)
            existing = getattr(req, "headers", None) or {}
            if isinstance(existing, dict):
                merged.update({str(k): str(v) for k, v in existing.items()})
        merged.update({str(k): str(v) for k, v in rw["headers"].items()})
        kwargs["headers"] = merged
    if rw.get("post_data") is not None:
        pd = rw["post_data"]
        kwargs["post_data"] = (
            json.dumps(pd, ensure_ascii=False) if isinstance(pd, (dict, list)) else str(pd)
        )
    if not kwargs:
        await _pass_through(route)
        return
    await route.continue_(**kwargs)


def _make_handler(page, rule: dict):
    """为单条规则构造 route handler（闭包持有 rule 引用以累计 hits）。"""

    async def _handler(route) -> None:
        method, url, post_data = await _read_request(route)
        try:
            if not _rule_matches(rule, method, url):
                await _pass_through(route)
                return

            state = _get_state(page)
            state["seq"] = _safe_int(state.get("seq"), 0) + 1
            rule["hits"] = _safe_int(rule.get("hits"), 0) + 1
            rule["last_url"] = url[:300]
            rule["last_method"] = method

            await _apply_delay(page, _safe_int(rule.get("delay_ms"), 0))

            action = str(rule.get("action", "") or "fulfill")
            if action == "passthrough":
                await _pass_through(route)
                return
            if action == "rewrite":
                await _do_rewrite(route, rule, method, url, post_data)
                return

            ctx = {
                "seq": state["seq"],
                "rng": state.get("rng") or random,
                "method": method,
                "url": url,
                "query": _parse_query(url),
                "json": _parse_json_body(post_data),
            }
            await _do_fulfill(route, rule, ctx)
        except Exception as e:  # noqa: BLE001
            _log.warning("[service_mock] handler 异常(放行请求): %s", str(e)[:120])
            await _pass_through(route)

    return _handler


async def apply_service_mock(engine, args: dict) -> ToolExecutionResult:
    """注册 / 清除 / 统计 mock 规则。

    Args:
        engine: BrowserCore engine（需可取到 page）。
        args: ``{"mode":"register"|"clear"|"stats", "rules":[...], "seed":int, ...}``
            单规则可将规则字段平铺在 args 顶层（省略 rules）。

    Returns:
        ToolExecutionResult（ok / message / data）。未启用 SERVICE_MOCK_ENABLED
        → ok=False（明确拒绝，不静默打桩）。
    """
    if not _enabled():
        return ToolExecutionResult(
            False, "外部依赖 mock 被禁用（SERVICE_MOCK_ENABLED=False），拒绝生效"
        )
    if not isinstance(args, dict):
        return ToolExecutionResult(False, "service_mock 参数必须为 dict")

    mode = str(args.get("mode", "") or "register").strip().lower()
    if mode not in _VALID_MODES:
        return ToolExecutionResult(
            False, f"未知 mock 模式: {mode}（{'/'.join(_VALID_MODES)}）"
        )

    page = _get_page(engine)
    if page is None:
        return ToolExecutionResult(False, f"无法执行 {mode}：engine 无 page 引用")

    if mode == "clear":
        removed = await _clear_mocks(page)
        return ToolExecutionResult(
            True, f"mock 规则已清除: {removed} 条", {"removed": removed}
        )

    if mode == "stats":
        stats = get_mock_stats(engine)
        if stats["rules"]:
            detail = ", ".join(
                f"{s['url_pattern']}×{s['hits']}" for s in stats["rules"]
            )
        else:
            detail = "无规则"
        return ToolExecutionResult(True, f"mock 命中统计: {detail}", stats)

    # ── register ──
    raw_rules = args.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        flat = {k: args[k] for k in _RULE_FIELDS if k in args}
        if not flat.get("url_pattern"):
            return ToolExecutionResult(
                False, "register 需要 rules 数组或顶层 url_pattern"
            )
        raw_rules = [flat]

    state = _get_state(page)
    seed = args.get("seed")
    if seed is not None:
        state["seed"] = seed
        state["rng"] = random.Random(seed)  # 可复现（B4 确定性友好）

    registered: list[str] = []
    try:
        for raw in raw_rules:
            if not isinstance(raw, dict):
                continue
            rule = _normalize_rule(raw)
            if not rule["url_pattern"]:
                return ToolExecutionResult(False, "mock 规则缺少 url_pattern")
            glob = _route_glob(rule["url_pattern"])
            await page.route(glob, _make_handler(page, rule))
            state["rules"].append(rule)
            state["patterns"].append(glob)
            registered.append(
                f"{rule['action']}:{rule['url_pattern']}"
                + (f"[{'/'.join(rule['methods'])}]" if rule["methods"] else "")
            )
        if not registered:
            return ToolExecutionResult(False, "未注册任何有效 mock 规则")
        return ToolExecutionResult(
            True,
            f"mock 已注册 {len(registered)} 条规则: " + "; ".join(registered),
            {"registered": registered, "total": len(state["rules"])},
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("[service_mock] 注册异常: %s", e)
        return ToolExecutionResult(False, f"注册异常: {type(e).__name__}: {e}")


async def _clear_mocks(page) -> int:
    """unroute 所有已注册 mock pattern 并清空规则表；返回清除条数。"""
    state = getattr(page, _STATE_ATTR, None)
    if not isinstance(state, dict):
        return 0
    patterns = list(state.get("patterns") or [])
    for pattern in patterns:
        with guard("_clear_mocks: await page.unroute(pattern)", log=_log):
            await page.unroute(pattern)
    removed = len(state.get("rules") or [])
    state["rules"] = []
    state["patterns"] = []
    state["seq"] = 0
    return removed


async def recover_service_mock(engine) -> None:
    """恢复：清除本 page 上所有 mock（用例间兜底，异常只记日志绝不阻断）。

    ★ SERVICE_MOCK_ENABLED=False 时直接返回 → 零行为变更。
    """
    if not _enabled():
        return
    page = _get_page(engine)
    if page is None:
        return
    with guard("recover_service_mock: removed = await _clear_mocks(page)", log=_log, level=logging.WARNING):
        removed = await _clear_mocks(page)
        if removed:
            _log.info("[service_mock] 用例间兜底清除 %d 条 mock 规则", removed)


def get_mock_stats(engine) -> dict[str, Any]:
    """读取 mock 命中统计（供断言消费；不改变任何状态）。

    Returns:
        ``{"total_hits":int, "rules":[{url_pattern, methods, action, hits,
        times, last_url, last_method}, ...]}``；无 page / 无规则 → 空统计。
    """
    page = _get_page(engine)
    state = getattr(page, _STATE_ATTR, None) if page is not None else None
    if not isinstance(state, dict):
        return {"total_hits": 0, "rules": []}
    rules = [
        {
            "url_pattern": r.get("url_pattern", ""),
            "methods": list(r.get("methods") or []),
            "action": r.get("action", ""),
            "hits": _safe_int(r.get("hits"), 0),
            "times": _safe_int(r.get("times"), 0),
            "last_url": r.get("last_url", ""),
            "last_method": r.get("last_method", ""),
        }
        for r in (state.get("rules") or [])
    ]
    return {"total_hits": sum(r["hits"] for r in rules), "rules": rules}


def mock_hit_count(engine, url_pattern: str = "") -> int:
    """某规则（或全部规则）的命中次数——最常用的断言入口。

    Args:
        engine: BrowserCore engine。
        url_pattern: 规则的 url_pattern；空 = 汇总所有规则。

    Returns:
        命中次数（无匹配规则 → 0）。
    """
    stats = get_mock_stats(engine)
    if not url_pattern:
        return _safe_int(stats.get("total_hits"), 0)
    target = str(url_pattern).strip()
    return sum(
        r["hits"] for r in stats["rules"] if str(r.get("url_pattern", "")) == target
    )


def register_service_mock_handlers(handlers: dict) -> None:
    """向 _single._TOOL_HANDLERS 注入 "service_mock" 分发（幂等）。"""
    if handlers is None:
        return
    if "service_mock" in handlers:
        return
    handlers["service_mock"] = "service_mock"  # 占位：实际由 _single 内联适配
