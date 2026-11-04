# -*- coding: utf-8 -*-
"""通用用例执行器：把 xlsx 用例逐条真实下发到引擎执行器。

核心真实链路：
    用例「关键操作指令」/「关键校验断言指令」
        → 引擎 CHECK_TAXONOMY / ACTION_TAXONOMY 解析为 tool_name
        → 引擎 _execute_tool(tool_name, args, engine)   ← 引擎真实执行入口
        → ToolExecutionResult(ok, content)
        → 与用例「预期结果」语义比对 → PASS/FAIL/ERROR

纪律（禁止造假）：
  - tool_name 解析不出来 → 记 NO_HANDLER，如实计为引擎缺口，绝不静默 PASS
  - 执行抛原生异常 → 记 ERROR（引擎应 fail-closed 返回 Result 而非 raise）
  - 期望无法从用例语义确定 → 记 UNDECIDED，只验证「不崩溃且有明确返回」，不冒充 PASS
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.pipeline.steps.step4.browser_tools.execution._single import _execute_tool
from scripts.case_runner.engine_tools import engine_tool_names
from scripts.case_runner._argspec import extract as _argspec_extract

# 参数契约（从引擎源码自动提取，运行期不写死）
ARGSPEC = _argspec_extract()

_TAX_PATH = os.path.join(
    _ROOT, "src", "pipeline", "steps", "step4", "browser_tools", "_taxonomy.py"
)


def _taxonomy():
    spec = importlib.util.spec_from_file_location("_cr_tax", _TAX_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# 1) 用例语义 → 期望
# ---------------------------------------------------------------------------
_TRUE_MARKERS = (
    "判真", "正向", "等于界值", "读侧=写侧", "各自独立判真",
    "分页/限流下仍精确判真", "大规模数据", "并发/多断言", "断言幂等",
    "大小写/编码", "特殊字符/XSS", "跨轨交叉", "断言信息超长", "边界值",
    # 操作指令轨：正常基线类
    "正向基线", "并发快速触发", "重复执行(幂等)", "重复提交幂等", "大规模载荷",
)
_FALSE_MARKERS = (
    "判伪", "目标缺失", "越界", "非法", "拒绝",
    # 操作指令轨：应当失败/拦截类
    "元素不可交互", "等待超时", "无鉴权", "越权", "资源不存在", "非法/缺失字段",
    "目标元素缺失", "非法选择器",
)
# 语义是「应诚实降级 / 应归因而非误判」，不能简单用 TRUE/FALSE 断言
_DEGRADE_MARKERS = ("断言器不可用", "NO_DATA", "不可评估")
_UNDECIDED_MARKERS = ("断言超时", "空值(null)")


def expectation_of(case: dict) -> str:
    """从「变化点」+「预期结果」推导期望：TRUE / FALSE / DEGRADE / UNDECIDED。

    UNDECIDED = 该变化点的语义是「归因正确 / 处理正确」而非「断言成立与否」，
    此时只验证「不崩溃 + 返回明确判定」，不冒充 PASS（禁止假绿）。
    """
    var = str(case.get("变化点", "") or "")
    exp = str(case.get("预期结果", "") or "")

    if any(k in var for k in _DEGRADE_MARKERS):
        return "DEGRADE"
    if any(k in var for k in _UNDECIDED_MARKERS):
        return "UNDECIDED"
    if any(k in var for k in _FALSE_MARKERS):
        return "FALSE"
    if any(k in var for k in _TRUE_MARKERS):
        return "TRUE"
    if "FAIL" in exp or "guard 拦截" in exp or "拦截(" in exp:
        return "FALSE"
    if "判定 PASS" in exp or "断言 PASS" in exp or "门禁 PASS" in exp:
        return "TRUE"
    return "UNDECIDED"


# ---------------------------------------------------------------------------
# 2) 变化点 → 参数变形（通用，非用例专属补丁）
# ---------------------------------------------------------------------------
XSS_PAYLOAD = "<script>alert(1)</script>"

def _long(n=5000):
    return "A" * n


async def _real_text(page, selector):
    try:
        loc = page.locator(selector).first
        t = await loc.inner_text(timeout=3000)
        return (t or "").strip()
    except Exception:
        try:
            return (await page.locator(selector).first.text_content(timeout=3000) or "").strip()
        except Exception:
            return ""


_MISS_SEL = "#__case_runner_not_exist__"
_OK_SEL = "body"

# 同名参数的合法枚举随断言而异（引擎各 _verify_* 白名单不同），按工具覆盖。
_TOOL_ARG_OVERRIDE: dict[str, dict] = {
    "verify_element_attribute_all": {"operator": "all"},
    "verify_list_order": {"data_type": "numeric", "order": "asc"},
    "verify_animation_complete": {"animation_type": "all"},
    "verify_loading_complete": {"expected_state": "hidden", "wait_network_idle": False},
    "verify_toast": {"check_type": "appear"},
    "verify_api_call_count": {"operator": "gte"},
    "verify_element_count": {"operator": "gte"},
    "verify_db_record_count": {"operator": "gte"},
    "verify_db_query": {"operator": "gte"},
    "verify_db_field_value": {"operator": "eq"},
    "verify_network": {"operator": "gte"},
    "verify_storage": {"operator": "not_exists"},
    "verify_style": {"check_type": "css_property"},
}


def _truthy_for(tool_name: str, case: dict, page_text: str, page_url: str):
    """判真场景下 expected 应取的值（让断言真实成立）。"""
    if tool_name in (
        "verify_element_exists_check", "verify_element_exists",
        "verify_element_visible", "verify_element_enabled",
        "verify_element_checked", "verify_image_loaded",
    ):
        return True
    if tool_name in ("verify_text_contains", "verify_text_in_page"):
        return (page_text[:20] or "患者").strip()
    if tool_name == "verify_element_text_equals":
        return (page_text[:40] or "患者").strip()
    if tool_name == "verify_text_regex":
        return "."
    if tool_name == "verify_url":
        return page_url or ""
    if tool_name in ("verify_element_attribute", "verify_element_attribute_all"):
        return ""
    if tool_name == "verify_select_value":
        return ""
    if tool_name == "verify_style":
        return "block"
    if tool_name == "verify_no_error_text":
        return []
    if tool_name == "verify_no_console_error":
        return []
    if tool_name == "verify_refresh":
        return False
    return None


async def build_args(tool_name: str, check_type: str, case: dict, page) -> dict:
    """按「引擎源码提取的参数契约」+ 变化点构造真实入参。

    参数全集来自 _argspec（代码事实），不再手写猜测 —— 消除「缺少 xxx 参数」类假失败。
    判真用页面真实文本/URL/元素，判伪用确定不存在的目标。
    """
    var = str(case.get("变化点", "") or "")
    want_missing = any(k in var for k in ("目标缺失", "判伪", "越界", "非法"))

    # ★ API 方法族动作（api_get/post/put/delete/patch → api_request）：
    #   method 语义由指令名携带；url/裁决由 _exec_api_case 按变化点语义处理
    if tool_name == "api_request":
        method = _API_METHODS.get((check_type or "").split("/")[0].strip(), "GET")
        return {"method": method}

    # ★ 身份切换动作：默认切回目标主身份（真实会话重建；多身份需身份池注册）
    if tool_name == "identity_switch":
        try:
            from src.core.config_loader import config as _cfg

            ident = (_cfg.target_username or "admin").strip()
        except Exception:
            ident = "admin"
        from scripts.case_runner._env import SUT_BASE, SUT_USER, SUT_PASS

        # username/password = 驱动器会话登录同一凭据源（与 Cookie 注入同源，非伪造）
        return {
            "identity": ident,
            "base_url": SUT_BASE,
            "username": SUT_USER,
            "password": SUT_PASS,
        }

    # ★ 故障注入/服务桩：模式与规则参数按动作语义给合法值（引擎 fail-closed 校验）
    if tool_name == "network_fault":
        a = {"mode": "offline", "selector": "", "target_hint": "body"}
        if want_missing:
            a = {"mode": "offline", "selector": "", "target_hint": "body"}
        return a
    if tool_name == "service_mock":
        return {
            "mode": "register",
            "url_pattern": "case_runner://mock/**",
            "action": "fulfill",
            "status": 200,
            "body": '{"mocked": true, "source": "case_runner"}',
        }

    # 源码里的实现函数名可能是 verify_xxx 或 _verify_xxx（适配两种登记形态）
    spec_keys = (
        ARGSPEC.get(tool_name) or ARGSPEC.get("_" + tool_name) or {}
    ).get("keys", [])
    if not spec_keys:
        # 动作类或未登记：走最小语义集
        if tool_name.startswith("browser_"):
            # 按变化点变形：正向用页面真实可见目标；目标缺失/非法用不存在或非法选择器
            if want_missing:
                a = {"target_hint": "text=__case_runner_不存在目标__"}
            elif "非法" in var or "注入" in var:
                a = {"target_hint": "text=<<<>>>${__非法选择器__}"}
            else:
                a = {"target_hint": "text=患者"}
            if tool_name in ("browser_fill", "browser_type_text"):
                a["text"] = "case_runner_probe"
            if tool_name in ("browser_select_option", "browser_select"):
                # ★ 通用修复: 下拉选择必须作用于真实 <select>——从当前页取第一个
                #   select 及其首个非空 option（零靶子硬编码），否则引擎对非 select
                #   元素如实报错（Element is not a <select>）属正确行为，错在驱动选目标。
                #   选择器生成规避引号（部分选择器解析器不支持引号内嵌）：
                #   data-testid 优先 → #id → select[name=x]（无引号）→ 裸 select
                try:
                    info = await page.evaluate(
                        "() => { const s = document.querySelector('select');"
                        " if (!s) return null;"
                        " const opt = Array.from(s.options).find(o => (o.text||'').trim());"
                        " const tid = s.getAttribute('data-testid');"
                        " const sel = tid ? '[data-testid=' + tid + ']'"
                        "   : (s.id ? '#' + s.id"
                        "   : (s.name ? 'select[name=' + s.name + ']' : 'select'));"
                        " return {sel: sel, opt: opt ? opt.text : ''}; }"
                    )
                except Exception:
                    info = None
                if isinstance(info, dict) and info.get("sel"):
                    # selector 参数走引擎 locator 的预解析 CSS 精确定位（Phase 1）；
                    # target_hint 同值兜底
                    a = {
                        "selector": info["sel"],
                        "target_hint": info["sel"],
                        "option_text": info.get("opt", ""),
                    }
                else:
                    a = {"target_hint": "text=患者", "option_text": ""}
            if tool_name in ("browser_press_key", "browser_key_down", "browser_key_up"):
                a["key"] = "Enter"
            return a
        return {"selector": _MISS_SEL if want_missing else _OK_SEL}

    page_text = await _real_text(page, "body")
    page_url = page.url if page else ""

    # ★ 语义反转：「不存在」类断言的判真条件是目标确实不存在，
    #   若沿用「判真=用存在的元素」会方向搞反（驱动器逻辑缺陷，非引擎缺陷）。
    is_negative = "not_exists" in tool_name or tool_name in (
        "verify_text_not_exists", "verify_db_record_not_exists",
    )
    if is_negative:
        sel = _OK_SEL if want_missing else _MISS_SEL
    else:
        sel = _MISS_SEL if want_missing else _OK_SEL
    bool_expected = False if want_missing else True

    # 按「参数名语义」填充（通用规则，随引擎新增参数名自动可扩展）
    sem: dict[str, Any] = {
        "selector": sel,
        "ui_selector": _OK_SEL,
        "value_selector": "td",
        "loading_selector": ".loading",
        "expected": _truthy_for(tool_name, case, page_text, page_url),
        "expect_text": "__case_runner_absent__" if want_missing else (page_text[:20] or "患者"),
        "not_expected": [],
        "expected_count": 0 if want_missing else 1,
        "operator": "eq",
        # DB 轨连的是引擎元数据库，须用其中真实存在的表（否则 Table doesn't exist）
        "table": "executions",
        "db_table": "executions",
        "where": {"1": 1},
        "db_where": {"1": 1},
        "field": "id",
        "db_field": "id",
        "sql": "SELECT COUNT(*) AS c FROM executions",
        "result_field": "c",
        "url_pattern": "/patients",
        "pattern": ".",
        "key": "__case_runner_key__",
        "storage_type": "localStorage",  # ★ 引擎默认即此大小写（传 localstorage 会判未知）
        "timeout_ms": 3000,
        "max_ms": 30000,
        "tolerance": 0,
        # 时间格式须匹配引擎 _verify_time 的 strptime 白名单
        "start_time": "2026-01-01 00:00:00",
        "end_time": "2026-12-31 23:59:59",
        "current_page_selector": ".pagination .active",
        "items_selector": "table tbody tr",
        "expected_page": 1,
        "expected_items_per_page": 20,
        # 合法枚举以引擎源码白名单为准（传非法值会被引擎 fail-closed 判 FAIL）
        "data_type": "numeric",
        "animation_type": "all",
        "expected_message": "x",
        "expected_relation": "eq",
        "value_source": "ui",
        "attribute": "id",
        "property": "display",
        "header_name": "Content-Type",
        "class_name": "",
        "check_type": "exists",
        "animation_type": "fade",
        "expected_state": "hidden",
        "expected_text": "",
        "expected_type": "alert",
        "text": "",
        "expected_filename": "",
        "expected_min_size": 0,
        "expected_field": "",
        "expected_value": "",
        "metric": "load",
        "data_type": "text",
        "order": "asc",
        "wait_network_idle": False,
        "selectors": [_OK_SEL],
        "rule": "visible",
        "min": 0,
        "max": 100000,
        "min_w": 0,
        "min_h": 0,
        "min_visible_ratio": 0.0,
        "min_overlap_area": 0,
        "code_field": "code",
        "status_ok": 200,
        "required_fields": [],
        "types": {},
        "formats": {},
        "enums": {},
    }

    args = {k: sem.get(k) for k in spec_keys if k in sem}
    # 契约里有但语义表未覆盖的参数 → 显式记空，避免引擎报缺参
    for k in spec_keys:
        args.setdefault(k, "")
    # 同名参数在不同断言里合法枚举不同（如 operator），按工具覆盖
    args.update(_TOOL_ARG_OVERRIDE.get(tool_name, {}))

    # ── 变化点注入（通用变形，作用在值型参数上）──
    def _inject(v):
        if "XSS" in var or "特殊字符" in var:
            return XSS_PAYLOAD
        if "超长" in var:
            return _long()
        if "空值" in var or "null" in var:
            return None
        if "大小写" in var or "编码" in var:
            return str(v).upper() if isinstance(v, str) else v
        if "边界" in var:
            return 0
        return v

    for k in ("expected", "text", "expected_text", "expect_text", "expected_message"):
        if k in args and isinstance(args[k], str):
            args[k] = _inject(args[k])

    # ★ verify_select_value 判真修正：作用于真实 <select> 并以其当前值作为期望
    #   （读回一致性语义；此前 selector=body 无 .value → 引擎如实报 None → 假 FAIL）
    if tool_name == "verify_select_value" and not want_missing:
        try:
            cur = await page.evaluate(
                "() => { const s = document.querySelector('select');"
                " return s ? String(s.value ?? '') : null; }"
            )
        except Exception:
            cur = None
        if cur is not None:
            args["selector"] = "select"
            args["expected"] = cur

    return args


# ---------------------------------------------------------------------------
# 3) 单条执行
# ---------------------------------------------------------------------------
def resolve_tool(spec_instruction: str) -> tuple[str, str]:
    """指令名 → (tool_name, 备注)。解析不出返回 (None, reason)。

    解析优先级（先权威执行分派表，再注册表）：
      1. 指令名本身即引擎可下发工具（_TOOL_HANDLERS 键）→ 直接用
      2. CHECK_TAXONOMY / ACTION_TAXONOMY 的 key 或 alias → 取其 tool_name
      3. 仍无 → 如实报缺口
    """
    s = (spec_instruction or "").strip()
    if not s:
        return None, "空指令"

    supported = engine_tool_names()

    # 1) 引擎分派表直接支持
    if s in supported:
        return s, ""

    tax = _taxonomy()

    # 2) 注册表 key / alias → tool_name
    for key, meta in tax.CHECK_TAXONOMY.items():
        if s == key or s in (meta.get("aliases") or []):
            tn = (meta.get("tool_name") or "").strip()
            if tn and tn in supported:
                return tn, ""
            if tn:
                return tn, f"tool_name {tn} 不在引擎分派表（注册表漂移）"
            return None, f"CHECK_TAXONOMY[{key}] 未声明 tool_name"

    for key, meta in tax.ACTION_TAXONOMY.items():
        if s == key:
            tn = (meta.get("tool_name") or "").strip()
            if tn and tn in supported:
                return tn, ""
            if tn:
                return tn, f"tool_name {tn} 不在引擎分派表（注册表漂移）"

    # 3) 命名规范化回退（通用规则，不针对单个名字打补丁）：
    #    用例集按 <族前缀>_<语义名> 拼装指令名，而引擎工具名并不总是该拼装形式——
    #      verify_element_exists   vs 引擎 verify_element_exists_check
    #      verify_url_matches      vs 引擎 verify_url
    #      browser_select_option   vs 引擎 browser_select
    #      browser_network_fault   vs 引擎 network_fault（无族前缀）
    #    剥离族前缀后按注册表 key/alias 再解析一次即可对齐，避免逐个名字硬编码。
    for _prefix in ("verify_", "browser_", "api_"):
        if s.startswith(_prefix):
            stripped = s[len(_prefix) :]
            if stripped:
                return resolve_tool(stripped)

    return None, f"无法解析为引擎工具: {s}"


_API_METHODS = {
    "api_get": "GET",
    "api_post": "POST",
    "api_put": "PUT",
    "api_delete": "DELETE",
    "api_patch": "PATCH",
}


def _pick_api_endpoint(env, method: str) -> tuple[str, Any]:
    """从引擎真实网络日志取同源 API 观测端点（通用，零靶子硬编码）。

    返回 (相对路径, 观测到的请求体dict|None)。无观测返回 ("", None)。
    """
    from scripts.case_runner._env import SUT_BASE

    logs = getattr(env.engine, "_network_logs", None) or []
    reqs = [
        e
        for e in logs
        if isinstance(e, dict)
        and e.get("event") == "request"
        and str(e.get("url", "")).startswith(SUT_BASE)
    ]
    if not reqs:
        return "", None
    same = [e for e in reqs if str(e.get("method", "")).upper() == method]
    if same:
        pick = same[-1]
    elif method == "GET":
        # GET 可回退到最近同源请求（页面导航本身也是 GET 语义）
        pick = reqs[-1]
    else:
        # ★ 诚实纪律: 非 GET 方法未观测到同源端点时返回空——
        #   把页面 URL 硬套给 PUT/DELETE 只会得到与用例语义无关的 404
        return "", None
    url = str(pick.get("url", ""))
    path = url.split("?", 1)[0]
    if path.startswith(SUT_BASE):
        path = path[len(SUT_BASE) :] or "/"
    post = pick.get("post_data")
    return path, (post if isinstance(post, (dict, list)) else None)


def _parse_status(content: str):
    m = re.search(r"status=(\d{3})", str(content or ""))
    return int(m.group(1)) if m else None


async def _exec_api_case(env, case: dict, row: dict) -> dict:
    """API 方法族用例：引擎 api_request 发真实 HTTP，按变化点语义裁决。

    裁决纪律（不冒充、不冤枉）：
      - 引擎应「如实上报状态码并结构化归因」；SUT 侧前置不可构造（如未启鉴权、
        当前身份即最高权限）→ UNDECIDED 并注明，不算引擎 FAIL；
      - 可构造前置下的如实上报按用例期望方向裁决。
    """
    from scripts.case_runner._env import SUT_BASE

    var = str(case.get("变化点", "") or "")
    first = str(case.get("关键操作指令", "") or "").split("/")[0].strip()
    method = _API_METHODS.get(first, "GET")
    path, observed_body = _pick_api_endpoint(env, method)
    if not path:
        if method == "GET":
            row["verdict"] = "UNDECIDED"
            row["error"] = "引擎网络日志无同源 API 观测端点（需先真实浏览页面）"
        else:
            row["verdict"] = "UNDECIDED"
            row["error"] = (
                f"网络日志未观测到 {method} 的同源端点（靶子无该 API 表面），"
                "方法级前置不可构造——非引擎缺陷"
            )
        return row

    auth = dict(getattr(env, "auth_headers", {}) or {})

    async def _call(extra: dict):
        args = {"method": method, "base_url": SUT_BASE, "url": path}
        args.update(extra)
        return await _execute_tool("api_request", args, env.engine)

    # ── 幂等：同一请求发两次，状态与 ok 必须一致 ──
    if ("幂等" in var) or ("重复提交" in var):
        extra = {"headers": auth}
        if observed_body and method not in ("GET", "HEAD"):
            extra["json"] = observed_body
        r1 = await _call(extra)
        r2 = await _call(extra)
        s1 = _parse_status(getattr(r1, "content", ""))
        s2 = _parse_status(getattr(r2, "content", ""))
        same = s1 is not None and s1 == s2
        row["actual"] = bool(getattr(r1, "ok", None))
        row["content"] = f"幂等两次 status: {s1} / {s2}（端点 {path}）"
        row["verdict"] = (
            "PASS"
            if (same and getattr(r1, "ok", None) == getattr(r2, "ok", None))
            else "FAIL"
        )
        if not same:
            row["error"] = "同请求两次结果不一致（幂等破坏）"
        return row

    # ── 慢响应/超时：1ms 超时 → 引擎须结构化归因 API_TIMEOUT ──
    if ("慢响应" in var) or ("超时" in var):
        r = await _call({"headers": auth, "timeout_ms": 1})
        c = str(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = c[:280]
        if "API_TIMEOUT" in c:
            row["verdict"] = "PASS"
        elif _parse_status(c) is not None:
            row["verdict"] = "UNDECIDED"
            row["error"] = "请求在 1ms 超时内完成，超时前置未真实构造"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 无鉴权：剥离鉴权头 → 期望 401/403 拒绝且不被误标 PASS ──
    if "无鉴权" in var:
        r = await _call({})
        s = _parse_status(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = str(getattr(r, "content", ""))[:280]
        if s in (401, 403):
            row["verdict"] = "PASS" if not getattr(r, "ok", True) else "FAIL"
            if getattr(r, "ok", None):
                row["error"] = "401/403 被引擎误标 ok=True（假PASS）"
        elif s is not None and s < 400:
            row["verdict"] = "UNDECIDED"
            row["error"] = f"SUT 该端点未启鉴权(status={s})，无鉴权前置不可构造"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 非法/缺失字段：坏载荷 → 期望 4xx 校验失败 ──
    if ("非法" in var) or ("缺失" in var):
        if method in ("GET", "HEAD"):
            r = await _call({"headers": auth, "params": {"__cr_bad__": "\x00"}})
        else:
            r = await _call({"headers": auth, "json": {"__cr_invalid__": "\x00"}})
        s = _parse_status(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = str(getattr(r, "content", ""))[:280]
        if s is not None and 400 <= s < 500:
            row["verdict"] = "PASS"
        elif s is not None and s < 400:
            row["verdict"] = "UNDECIDED"
            row["error"] = f"SUT 未校验该载荷(status={s})，引擎如实上报——靶子侧校验缺失"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 资源不存在：404 业务边界 ──
    if "资源不存在" in var:
        r = await _call({"headers": auth, "url": path.rstrip("/") + "/__cr_missing__"})
        s = _parse_status(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = str(getattr(r, "content", ""))[:280]
        if s == 404:
            row["verdict"] = "PASS"
        elif s is not None and s < 400:
            row["verdict"] = "UNDECIDED"
            row["error"] = f"缺失资源未被判 404(status={s})——靶子侧路由语义"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 越权：当前驱动身份即最高权限，前置通常不可构造 ──
    if "越权" in var:
        r = await _call({"headers": auth})
        s = _parse_status(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = str(getattr(r, "content", ""))[:280]
        if s in (401, 403):
            row["verdict"] = "PASS"
        elif s is not None and s < 400:
            row["verdict"] = "UNDECIDED"
            row["error"] = "当前身份即最高权限，越权前置不可构造（需身份池注册低权身份）"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 大规模载荷：超大参数 → 结构化完成、不崩溃 ──
    if "大规模" in var:
        big = "A" * 100000
        if method in ("GET", "HEAD"):
            r = await _call({"headers": auth, "params": {"q": big}})
        else:
            r = await _call({"headers": auth, "json": {"q": big}})
        s = _parse_status(getattr(r, "content", ""))
        row["actual"] = bool(getattr(r, "ok", None))
        row["content"] = str(getattr(r, "content", ""))[:280]
        if s is not None and s < 400:
            row["verdict"] = "PASS"
        elif s is not None and s < 500:
            row["verdict"] = "UNDECIDED"
            row["error"] = f"大载荷被拒(4xx status={s})——引擎如实上报，边界语义属靶子"
        else:
            row["verdict"] = "FAIL"
        return row

    # ── 正向（缺省）：带鉴权真实请求 → 期望 2xx ──
    extra = {"headers": auth}
    if observed_body and method not in ("GET", "HEAD"):
        extra["json"] = observed_body
    r = await _call(extra)
    s = _parse_status(getattr(r, "content", ""))
    row["actual"] = bool(getattr(r, "ok", None))
    row["content"] = str(getattr(r, "content", ""))[:280]
    if s is not None and 200 <= s < 300:
        row["verdict"] = "PASS"
    elif s == 405:
        row["verdict"] = "UNDECIDED"
        row["error"] = f"观测端点不支持 {method}(405)，方法正向前置不可构造——非引擎缺陷"
    else:
        row["verdict"] = "FAIL"
    return row


async def exec_one(env, case: dict) -> dict:
    """真实执行一条用例，返回证据行。"""
    row = {
        "case_id": case.get("用例ID"),
        "功能域": case.get("功能域"),
        "子模块": case.get("子模块"),
        "变化点": case.get("变化点"),
        "指令": "",
        "tool": None,
        "expect": expectation_of(case),
        "actual": None,
        "content": "",
        "verdict": "",
        "error": "",
    }

    instr = str(case.get("关键操作指令", "") or "")
    first = instr.split("/")[0].strip()
    row["指令"] = instr

    tool, reason = resolve_tool(first)
    if tool is None:
        # 轨路由：指令不属于浏览器工具层（record_* / run_*_gate / apply_action_templates 等），
        # 走各自真实执行入口；仍不支持才如实记 NO_HANDLER
        tr = None
        try:
            from scripts.case_runner._tracks_exec import exec_track_instruction

            tr = await exec_track_instruction(instr, case, env)
        except Exception as e:
            tr = {"ok": False, "content": f"轨路由异常: {type(e).__name__}: {str(e)[:150]}", "track": "轨路由"}
        if tr is not None:
            row["tool"] = tr.get("track", "轨路由")
            row["actual"] = tr.get("ok")
            row["content"] = str(tr.get("content", ""))[:300]
            exp, act = row["expect"], tr.get("ok")
            if act is None:
                row["verdict"] = "UNDECIDED"
            elif exp == "FALSE":
                row["verdict"] = "PASS" if act is False else "FAIL"
            else:
                row["verdict"] = "PASS" if act else "FAIL"
            return row
        row["verdict"] = "NO_HANDLER"
        row["error"] = reason
        return row
    row["tool"] = tool

    # ★ API 方法族：专用执行路径（真实 HTTP + 变化点语义裁决）
    if tool == "api_request":
        try:
            return await _exec_api_case(env, case, row)
        except Exception as e:
            row["verdict"] = "ERROR"
            row["error"] = f"api_case 执行异常: {type(e).__name__}: {str(e)[:180]}"
            return row

    check_type = first
    try:
        args = await build_args(tool, check_type, case, env.page)
    except Exception as e:
        row["verdict"] = "ERROR"
        row["error"] = f"构造入参异常: {type(e).__name__}: {e}"
        return row

    t0 = time.time()
    var = str(case.get("变化点", "") or "")
    try:
        if ("幂等" in var) or ("重复执行" in var) or ("重复提交" in var):
            # 幂等：同状态重复执行两次，结果必须一致（真实比对，非声明）
            r1 = await _execute_tool(tool, args, env.engine)
            r2 = await _execute_tool(tool, args, env.engine)
            a1 = bool(getattr(r1, "ok", None))
            a2 = bool(getattr(r2, "ok", None))
            row["actual"] = a1
            row["content"] = f"幂等两次: {a1} / {a2}"
            row["idempotent_consistent"] = a1 == a2
        elif ("并发" in var) or ("多断言" in var):
            # 并发：真实并发下发 3 次，结果必须互不串扰且一致
            rs = await asyncio.gather(
                *[_execute_tool(tool, args, env.engine) for _ in range(3)]
            )
            acts = [bool(getattr(r, "ok", None)) for r in rs]
            row["actual"] = acts[0]
            row["content"] = f"并发3次: {acts}"
            row["concurrent_consistent"] = len(set(acts)) == 1
        else:
            res = await _execute_tool(tool, args, env.engine)
            row["actual"] = bool(getattr(res, "ok", None))
            row["content"] = str(getattr(res, "content", ""))[:300]
    except Exception as e:
        row["verdict"] = "ERROR"
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        row["elapsed_ms"] = int((time.time() - t0) * 1000)
        return row
    row["elapsed_ms"] = int((time.time() - t0) * 1000)

    exp = row["expect"]
    act = row["actual"]

    # 一致性要求（幂等/并发）：一致性不成立即为真实缺陷，不因单次结果正确而放行
    cons_ok = True
    if "idempotent_consistent" in row and not row["idempotent_consistent"]:
        cons_ok = False
    if "concurrent_consistent" in row and not row["concurrent_consistent"]:
        cons_ok = False

    if exp == "TRUE":
        row["verdict"] = "PASS" if (act is True and cons_ok) else "FAIL"
    elif exp == "FALSE":
        row["verdict"] = "PASS" if (act is False and cons_ok) else "FAIL"
    elif exp == "DEGRADE":
        row["verdict"] = "PASS" if (act is not None and cons_ok) else "FAIL"
    else:
        # 期望不确定：只验证「不崩溃且返回明确判定」，不冒充 PASS
        row["verdict"] = "UNDECIDED" if (act is not None and cons_ok) else "FAIL"
    if not cons_ok:
        row["error"] = (row.get("error") or "") + " [一致性不成立]"
    return row
