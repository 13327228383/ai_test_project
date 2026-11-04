# -*- coding: utf-8 -*-
"""API 轨校验：3 级响应校验 + 自动检测 — 从 verification.py 拆出，零业务逻辑变更。"""

import re as _re, json
from typing import Any
import logging

_log = logging.getLogger(__name__)

from ._url_match import _match_url_pattern

# 「调用计数类」check 类型（api_call_count / api_call_check 为历史别名）。
# 单一事实源：生成端契约判定与传输层 plan builder 共用，避免两处各自定义后漂移。
API_CALL_COUNT_CHECK_TYPES = frozenset(
    {"api_call_count_check", "api_call_count", "api_call_check"}
)


def _is_wildcard_call_probe(checks) -> bool:
    """checks 是否**全部**是"通配 url_pattern 的调用计数探测"（引擎自造、不可评估）。

    ★ D20 根因（2026-09-23）：Step3 规则 fallback 给安全类用例挂了一条
      `{"type": "api_call_count_check", "operator": ">=", "url_pattern": ".*",
        "expected_count": 1}`，expect 写作"执行期间系统触发了至少 1 次 API/XHR
      调用（证明后端联动）"。实测 TC-REQ-001-SEC01（executions.payload_json 实取）：
      该用例动作只有 `navigate /` + `screenshot`，靶子 ModSystem 是 SSR（form 提交 +
      整页渲染，无 XHR）→ 断言**在任何实现下都不可评估**，却被判 FAIL/PRODUCT_BUG。
      url_pattern=".*" 意味"随便调用一次就行"，这不是任何需求的性质，
      而是**实现方式**（SSR vs SPA）的性质 → 不得作为产品缺陷判据。

    返回 True 的条件（逐条保守，宁可不判也不假 Bug）：
      · checks 非空；且
      · 每一条都无法归约为"针对具体端点"的断言——即其 url_pattern 为通配
        （`.*` / `^.*$` / 空），type 为调用计数类。
      只要存在任一"具体端点"断言（url_pattern 含实际路径），返回 False，
      保持既有 FAIL 语义（真实"该调没调"仍是产品缺陷）。
    """
    if not checks:
        return False
    _wildcard = {".*", "^.*$", ".+", "^.+$", "", "*"}
    _count_types = API_CALL_COUNT_CHECK_TYPES
    for _c in checks:
        if not isinstance(_c, dict):
            return False
        _inner = _c.get("check") if isinstance(_c.get("check"), dict) else _c
        _t = str(_inner.get("type") or "").strip().lower()
        if _t not in _count_types:
            return False
        _pat = str(_inner.get("url_pattern") or _inner.get("pattern") or "").strip()
        if _pat not in _wildcard:
            return False
    return True


async def _verify_api(
    api_tracker,
    start_time: float,
    checks: list[str],
    response_schemas: dict = None,
    step_desc: str = "",
) -> dict[str, Any]:
    """
    增强版 API 校验（★ T03, 2026-08-06: 薄适配层 → ApiTrackVerifier）。

    Level 1（现有）: 状态码 >= 400 → FAIL
    Level 2（★ P1-3新增）: 状态码 200 但响应体业务 code ≠ 0/success → WARNING
    Level 3（★ P1-3新增）: 必填字段缺失 → WARNING
    ★ P2-2: 自动检测动作目标描述中的API关键字，无需显式写api_checks
    ★ T03: dict 分发（response_time/response_header→verify_perf、
      api_call_count/request_body/api_response_body→verify_api_ext、
      api_header→verify_header、network→verify_network、websocket→verify_websocket、
      page_load_time→verify_page_load）；字符串 check 保留 _match_url_pattern；
      未知 dict → UNSUPPORTED_CHECK（fail-closed，消除静默跳过假通过）。
    """
    from ._track_protocol import ApiTrackVerifier

    calls = api_tracker.get_calls_since(start_time) if api_tracker else []
    if not calls:
        # ★ 收紧(2026-06-23)：区分"声明API却未发生"与"纯展示无声明"。
        #   checks 非空(Step3 verification_plan 声明了 api 断言)但无调用→fail(预期调用未发生,漏报);
        #   checks 为空(纯展示意图,prompt 允许 api 空数组)→保持 pass(无法判定,避免错杀)。
        if checks:
            # ★ D20（2026-09-23）：区分两种"无调用"——
            #   ① 断言对**本靶子实现**不适用（通配 url_pattern 的 XHR 计数探测，
            #      见 _is_wildcard_call_probe）⇒ 引擎侧不可评估，不得判产品缺陷；
            #   ② 声明了具体端点的调用却未发生 ⇒ 保持原 FAIL（真实漏调用是缺陷）。
            if _is_wildcard_call_probe(checks):
                return {
                    "passed": False,
                    "reason": "ASSERTION_NOT_APPLICABLE",
                    "evidence": (
                        "API断言不适用: 仅声明了通配 url_pattern 的『必须有API/XHR调用』"
                        "计数探测，而本用例动作不含可触发 XHR 的交互、"
                        "或靶子为非 XHR(SSR)实现 → 断言不可评估（引擎侧，非产品缺陷）"
                    ),
                    "calls": [],
                }
            return {
                "passed": False,
                "evidence": "声明了API校验点但无API调用发生(预期调用未发生)",
                "calls": [],
            }
        return {"passed": True, "evidence": "无API调用(纯前端操作,无声明)", "calls": []}

    evidence_parts = []
    all_passed = True

    # ★ P2-2: 如果没有显式checks但有动作目标描述，自动检测API关键字
    if not checks and step_desc:
        auto_checks = _auto_detect_api_checks(step_desc, calls)
        if auto_checks:
            checks = auto_checks

    # ★ T03: 协议统一分发（dict + 字符串 check）
    ctx = {"api_tracker": api_tracker, "start_time": start_time}
    _vr = await ApiTrackVerifier().verify(checks, ctx)
    if _vr.get("evidence"):
        evidence_parts.append(_vr.get("evidence", ""))
    if not _vr.get("passed"):
        all_passed = False

    # ★ P1-3: Level 2 — 响应体业务字段校验
    if response_schemas:
        for call in calls:
            url = call.get("url", "")
            resp_body = call.get("resp_body")  # api_tracker 使用 resp_body 字段
            if resp_body and url in response_schemas:
                schema = response_schemas[url]
                # 校验业务 code
                expected_code_path = schema.get("code_field", "code")
                actual_code = _extract_json_path(resp_body, expected_code_path)
                expected_ok = schema.get("status_ok", [0, 200])
                if actual_code is not None and actual_code not in expected_ok:
                    evidence_parts.append(
                        "业务code异常: %s → %s (期望: %s)"
                        % (url, actual_code, expected_ok)
                    )
                    # Level 2: WARNING 不改变 all_passed

    # ★ P1-3: Level 3 — 必填字段缺失校验
    if response_schemas:
        for call in calls:
            url = call.get("url", "")
            resp_body = call.get("resp_body")  # api_tracker 使用 resp_body 字段
            if resp_body and url in response_schemas:
                schema = response_schemas[url]
                for field in schema.get("required_fields", []):
                    val = _extract_json_path(resp_body, field)
                    if val is None:
                        evidence_parts.append("必填字段缺失: %s → %s" % (url, field))

    if not checks and calls:
        for c in calls:
            s = c.get("status", 0)
            u = c.get("url", "")
            if s >= 400:
                evidence_parts.append("API %s→%d(异常)" % (u, s))
                all_passed = False
            else:
                evidence_parts.append("API %s→%d" % (u, s))

    evidence = (
        "; ".join(p for p in evidence_parts if p) if evidence_parts else "API校验通过"
    )
    return {"passed": all_passed, "evidence": evidence, "calls": calls}


# ★ P2-2: 自动检测API关键字
_API_KEYWORD_PATTERNS = [
    # 中文API描述模式
    r"(?:调用|请求|获取|查询|提交|保存|删除|修改|更新|创建|新增)(?:了)?\s*(?:API|接口|数据)",
    r"(?:API|接口|请求)\s*(?:调用|返回|响应)",
    r"(?:GET|POST|PUT|DELETE|PATCH)\s+/\S+",
    # 英文API模式
    r"/api/\S+",
    r"(?:fetch|ajax|axios)\s*\(",
]


def _auto_detect_api_checks(step_desc: str, calls: list) -> list[str]:
    """
    从动作目标描述中自动检测API校验需求。
    如果动作目标描述提到API/接口/数据请求，且实际有HTTP调用发生，
    则自动生成api_checks。
    """
    if not step_desc or not calls:
        return []
    desc_lower = step_desc.lower()
    has_api_hint = False
    for pattern in _API_KEYWORD_PATTERNS:
        if _re.search(pattern, desc_lower):
            has_api_hint = True
            break
    if not has_api_hint:
        return []
    # 自动生成checks：对每个实际发生的API调用做状态码检查
    auto_checks = []
    for c in calls:
        url = c.get("url", "")
        method = c.get("method", "GET")
        status = c.get("status", 0)
        # 只对业务API生成check（跳过静态资源）
        if any(
            url.lower().endswith(e)
            for e in (".js", ".css", ".png", ".jpg", ".svg", ".ico")
        ):
            continue
        if "/api/" in url.lower() or status:
            auto_checks.append(
                "应调用 %s %s 返回200" % (method.upper(), url.split("?")[0][:80])
            )
    return auto_checks[:5]  # 最多5个


def _extract_json_path(data, path: str):
    """
    ★ P1-3: 从 JSON 数据中提取指定路径的值。
    支持路径如 "code", "data.id", "result.status"。
    ★ P2-2: 新增支持数组索引，如 "data.items[0].id", "list[2].name"。
    """
    if not data or not path:
        return None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return None

    # ★ P2-2: 支持数组索引解析
    # 分割路径：data.items[0].id → ['data', 'items', '0', 'id']
    parts = _re.split(r"\.|\[(.*?)\]", path)
    parts = [p for p in parts if p is not None and p != ""]

    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            except (ValueError, TypeError) as _e:
                _log.debug(
                    "[verify_api] 数组索引解析失败(§5 可能造假PASS/FAIL): %s", _e
                )
                return None
        else:
            return None
    return current
