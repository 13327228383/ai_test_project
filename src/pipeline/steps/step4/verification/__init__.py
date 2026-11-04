# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
3 轨校验编排器 (VerificationOrchestrator)
==========================================
在每个意图动作执行完成后，汇总 UI / API / DB 三条轨道的校验结果。

校验策略（3 轨均通过才算 PASS）：
  1. UI 轨 → AI verify 动作（browser_ai 中已有）
  2. API 轨 → ApiTracker 网络请求校验
  3. DB 轨  → DbVerifier 数据库记录校验

【2026-06-18 重构】原 487 行单文件按职责拆分：
- _url_match.py — _match_url_pattern
- _confidence.py — _compute_confidence
- _api.py — _verify_api / _auto_detect_api_checks / _extract_json_path
- _db_ui.py — _verify_db_async / _verify_ui_objective
- __init__.py — verify_step (主编排器) + re-export
"""

import asyncio, time, re as _re, json
from typing import Any
import logging

_log = logging.getLogger(__name__)

# ── 从拆分子模块 import（也作为 re-export 维持原 API） ──
from ._url_match import _match_url_pattern
from ._confidence import _compute_confidence
from ._api import (
    _verify_api,
    _auto_detect_api_checks,
    _extract_json_path,
    _API_KEYWORD_PATTERNS,
)
from ._db_ui import _verify_db_async, _verify_ui_objective
from ._tracks import infer_required_tracks

# ★ 2026-07-02 扩展：新增断言验证器
from ._verify_style import verify_style
from ._verify_position import verify_position
from ._verify_perf import verify_performance
from ._verify_refresh import verify_refresh
from ._verify_toast import verify_toast

# ★ 2026-07-02 扩展第二批：新增断言验证器
from ._verify_count import verify_count
from ._verify_state import verify_state
from ._verify_db_ext import verify_db_ext
from ._verify_api_ext import verify_api_ext
from ._verify_image import verify_image

# ★ T03(2026-08-06): 单轨校验器协议 + 三轨收敛
from ._track_protocol import (
    TrackCheckResult,
    TrackResult,
    TrackVerifier,
    UiTrackVerifier,
    ApiTrackVerifier,
    DbTrackVerifier,
)

# ★ F7 (2026-08-16): 接口契约（响应结构/schema）汇总层检测（fail-open）
from ._api_contract import _flag_api_contract, _validate_contract_body

# ★ F8/F2/F3/F9/F10 (2026-08-16): 操作质量（幂等/响应性能/并发竞态/网络/变异）汇总层检测（fail-open）
from ._op_quality import (
    _flag_idempotency,
    _flag_performance,
    _flag_concurrency,
    _flag_network,
    _flag_mutation_scan,
)

# ★ F1 (2026-08-16): 安全完整（XSS 反射 / 敏感数据泄露）汇总层检测（fail-open）
from ._security_full import _flag_xss_reflected, _flag_sensitive_leak

# ★ a11y (2026-08-16): 页面可访问性维度汇总层检测（fail-open）
from ._a11y_gate import _flag_a11y, _a11y_declared


# ═══════════════════════════════════════════════════════════════════════════
# ★ GAP-08 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]
#   配置隐藏(config_hidden)疑似标注 —— 汇总层跨轨增强。
#
# 背景：盲测证明 config_hidden 类缺陷 0% 检出(27 个未捕获缺陷之一，落在"配置"维度)。
#   本质：数据在源(DB/API)存在，但被配置/feature-flag 压制不展示 → 引擎假绿。
#   既有 GAP-02(_db_ui._assert_field_rendered)已能捕获"字段没渲染"(→ [render_blank] FAIL)，
#   但无法区分「源确有数据却被隐藏」(config_hidden) 与「纯模板缺陷」(普通 render_blank)。
#
# 本增强(有界/fail-open)：在汇总层，当 UI 轨因 render_blank 失败(字段未渲染) 且
#   源轨(DB/API)确有数据(存在性/值断言 PASS 且带回数据) 时，额外标注 [config_hidden_suspect]。
#   仅加标记，不改动既有 FAIL 语义，不误杀：源轨无数据/未声明 → 维持 render_blank FAIL，不标。
#   引擎无 flag 知识，故标注为「疑似」(suspect)，交由人工/LLM(GAP 复核) 终判。
# ═══════════════════════════════════════════════════════════════════════════
_CONFIG_HIDDEN_TAG = "[config_hidden_suspect]"


def _source_track_confirms_data(track: dict | None) -> bool:
    """源轨(DB/API)是否确实带回了数据(存在性/值断言通过且有证据)。

    判定：track 存在且 PASS(DB 轨 data 非空，或 API 轨 calls 非空)。
    默认 passed=True 但无证据(skipped/UNKNOWN/无对应 checks) → False，
    避免把「未核验源」误判为「源有数据」。
    """
    if not isinstance(track, dict):
        return False
    # 兼容有无 status 字段两种结构：以 status>PASS 或 passed=True 视为通过
    _status_pass = False
    try:
        from .tracks import TrackStatus

        _status_pass = TrackStatus.parse(track.get("status")) == TrackStatus.PASS
    except Exception:
        _status_pass = bool(track.get("passed"))
    if not _status_pass:
        return False
    _data = track.get("data") or []
    _calls = track.get("calls") or []
    return bool(_data) or bool(_calls)


def _flag_config_hidden_suspect(result: dict) -> dict:
    """★ GAP-08 [已复核-DeepSeek-V3 2026-08-16]：为 config_hidden 类疑似加标记。

    Args:
        result: verify_step 汇总后的结果(含 ui/api/db 三轨)。

    Returns:
        result(原地增强)：
          - 命中：置 result["config_hidden_suspect"]=True，并在
            result["ui"]["evidence"] 与 result["summary"] 追加 [config_hidden_suspect]；
          - 未命中：result["config_hidden_suspect"]=False，保持原样(含 render_blank FAIL)。

    判定(两条件 AND，fail-open)：
      ① UI 轨失败且证据含 [render_blank](GAP-02 已确认字段未渲染)；
      ② DB 或 API 任一源轨确认有数据(_source_track_confirms_data)。
    仅二者同时满足才标 config_hidden_suspect；否则维持既有 render_blank FAIL(不误杀)。
    """
    if not isinstance(result, dict):
        return result
    _ui = result.get("ui") or {}
    _ui_evidence = _ui.get("evidence", "") or ""
    _ui_failed = not _ui.get("passed")
    _ui_render_blank = "[render_blank]" in _ui_evidence
    if not (_ui_failed and _ui_render_blank):
        result["config_hidden_suspect"] = False
        return result
    _source_has_data = _source_track_confirms_data(
        result.get("db")
    ) or _source_track_confirms_data(result.get("api"))
    if not _source_has_data:
        # 源轨无数据/未声明 → 维持 render_blank FAIL，不标 config_hidden(避免误判普通模板缺陷)
        result["config_hidden_suspect"] = False
        return result
    result["config_hidden_suspect"] = True
    if _CONFIG_HIDDEN_TAG not in _ui_evidence:
        _ui["evidence"] = (_ui_evidence + " " + _CONFIG_HIDDEN_TAG)[:500]
    _summary = result.get("summary", "") or ""
    if _CONFIG_HIDDEN_TAG not in _summary:
        result["summary"] = (_summary + " " + _CONFIG_HIDDEN_TAG)[:500]
    _log.info(
        "[GAP-08] config_hidden 疑似: UI 渲染缺失(render_blank) 且 源轨确认有数据"
        " → 标注 %s",
        _CONFIG_HIDDEN_TAG,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# ★ GAP-05 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)]
#   安全 / 越权绕过(security_authz_bypass)检测 —— 汇总层跨轨增强(复用 GAP-08 模式)。
#
# 背景：盲测证明 security_bypass 类缺陷 0% 检出(27 个未捕获缺陷之一，落在"安全"维度，
#   6/6 真实缺陷全漏)。本质：某操作本应仅高权限角色可为(非 admin → 403)，缺陷把鉴权
#   判断整体跳过；而引擎始终以靶子默认 admin 身份点按钮，永远走"有权限"分支 →
#   缺陷与正常实现的观测结果完全一致 → 引擎零感知。
#
# 引擎能力差距(实测)：src/ 全库无任何「未授权身份/auth 上下文」机制(grep
#   authz|unauthorized 仅命中本平台自身后端路由的 403，属测试台侧而非被测侧)，
#   引擎无法自行"换一个低权限身份再调一次"。故本增强不虚构 auth 上下文，
#   只提供【最小可行标记能力】(有界)：
#     ① 用例可在 checks 中声明 security_check —— 与既有 consistency_check 完全同构的
#        可选新键(不改数据契约；未声明 → 零影响、零开销)：
#          checks["security_check"] = {
#              "authz_gated": True,                 # 声明该操作为高权限门禁操作
#              "target": "/api/entities/*",         # 受保护端点(URL pattern，可选)
#              "methods": ["DELETE"],               # 受保护方法(可选)
#              "unauthorized_markers": ["role=viewer"],  # 判定「此调用为未授权身份」的标记
#              "assume_unauthorized": False,        # True=整步都在未授权身份下执行
#              "expect_denied_status": [401, 403],  # 期望的拒绝状态码
#              "fail_on_bypass": False,             # opt-in：命中时把 passed 置 False
#          }
#     ② 汇总层在 API 轨【已观测到】的调用中，筛出「命中受保护端点 且 带未授权身份标记」
#        的调用；若其返回成功(2xx 且 body 未显式失败) 而非期望拒绝 → 标 [security_authz_bypass]。
#   fail-open：默认仅加标记 + 追加证据，绝不改动既有 passed/FAIL 语义；
#   仅当用例显式 fail_on_bypass=True 才置 passed=False(opt-in，存量用例零影响)。
#   未观测到未授权调用 → 不标(inconclusive，明确记录「引擎无 auth 上下文，无法判定」，不误杀)。
# ═══════════════════════════════════════════════════════════════════════════
_AUTHZ_BYPASS_TAG = "[security_authz_bypass]"
_AUTHZ_DEFAULT_DENIED = (401, 403)


def _authz_call_status(call: dict) -> int:
    """安全取调用状态码(缺失/非法 → 0)。"""
    try:
        return int(call.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _authz_call_matches(
    call: dict, target: str = "", methods: list | None = None
) -> bool:
    """调用是否命中「受保护端点」声明(target URL pattern + methods，两者均可选)。"""
    if not isinstance(call, dict):
        return False
    _methods = [str(m).strip().upper() for m in (methods or []) if str(m).strip()]
    if _methods:
        _m = str(call.get("method", "") or "").upper()
        if _m and _m not in _methods:
            return False
    if target:
        try:
            return bool(_match_url_pattern(str(target), str(call.get("url", "") or "")))
        except Exception:
            return str(target).lower() in str(call.get("url", "") or "").lower()
    return True


def _authz_unauthorized_marker_hit(call: dict, markers: list) -> bool:
    """调用是否携带「未授权身份」标记。

    ★ GAP-05 RISK 缓解(2026-08-16) [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)]：匹配面收窄——
    不再对整条 url/请求头串做子串匹配(原实现会在非身份相关字段如 path 片段/静态头
    误命中标记子串)，只查 URL query 参数(键/值/键=值三种形态) 与请求体
    (JSON 顶层字符串值 / 字符串化)。大小写不敏感；"role=viewer" 这类标记
    仍可命中 query 的 "role=viewer"，兼容既有 security_authz_verify 用例。
    """
    _mk = [str(m).strip().lower() for m in (markers or []) if str(m).strip()]
    if not _mk:
        return False
    _hay_parts: list[str] = []
    # (a) URL query 参数：键、值、键=值 三种形态
    with guard("_authz_unauthorized_marker_hit: from urllib.parse import urlsplit, p", log=_log):
        from urllib.parse import urlsplit, parse_qsl

        _u = str(call.get("url", "") or "")
        for _k, _v in parse_qsl(urlsplit(_u).query, keep_blank_values=True):
            _hay_parts.append(str(_k))
            _hay_parts.append(str(_v))
            _hay_parts.append("%s=%s" % (_k, _v))
    # (b) 请求体：JSON 顶层字符串值(键/值/键=值) / 字符串化
    _body = call.get("req_body", "") or ""
    if isinstance(_body, dict):
        for _k, _v in _body.items():
            if isinstance(_v, str):
                _hay_parts.append(str(_k))
                _hay_parts.append(_v)
                _hay_parts.append("%s=%s" % (_k, _v))
    elif isinstance(_body, (str, bytes)):
        try:
            _dec = json.loads(_body)
            if isinstance(_dec, dict):
                for _k, _v in _dec.items():
                    if isinstance(_v, str):
                        _hay_parts.append(str(_k))
                        _hay_parts.append(_v)
                        _hay_parts.append("%s=%s" % (_k, _v))
            elif isinstance(_dec, list):
                for _it in _dec:
                    if isinstance(_it, str):
                        _hay_parts.append(_it)
        except Exception:
            _hay_parts.append(str(_body))
    _hay = " ".join(_hay_parts).lower()
    return any(m in _hay for m in _mk)


def _authz_response_succeeded(call: dict) -> bool:
    """调用是否「成功」(越权成功的判据)：2xx 且响应体未显式声明失败(ok=False/success=False)。"""
    _st = _authz_call_status(call)
    if not (200 <= _st < 300):
        return False
    _body = call.get("resp_body")
    if isinstance(_body, (str, bytes)):
        try:
            _body = json.loads(_body)
        except (ValueError, TypeError):
            _body = None
    if isinstance(_body, dict):
        for _k in ("ok", "success"):
            if _body.get(_k) is False:
                return False
    return True


def _flag_security_authz_bypass(result: dict, checks: dict | None = None) -> dict:
    """★ GAP-05 [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)]：为「越权绕过」加标记。

    Args:
        result: verify_step 汇总后的结果(含 ui/api/db 三轨)。
        checks: 本步 checks(可选)；声明位为 checks["security_check"]。
            省略时回落读取 result["security_check"]，故 _flag_security_authz_bypass(result)
            单参调用同样可用(与 _flag_config_hidden_suspect 保持同构)。

    Returns:
        result(原地增强)：
          - 命中：result["security_authz_bypass"]=True，api 轨 evidence 与 summary 追加
            [security_authz_bypass]，result["security_authz_evidence"] 记录证据明细；
            仅当声明 fail_on_bypass=True 才额外置 result["passed"]=False。
          - 未命中/未声明/无未授权调用：result["security_authz_bypass"]=False，原样返回。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("security_check") or result.get("security_check") or {}
    if not isinstance(_decl, dict) or not _decl.get("authz_gated"):
        # 未声明 authz-gated → 零影响(不标、不记证据)
        result["security_authz_bypass"] = False
        return result

    _api = result.get("api") or {}
    _calls = [c for c in (_api.get("calls") or []) if isinstance(c, dict)]
    _target = str(_decl.get("target") or _decl.get("url") or "")
    _methods = _decl.get("methods") or (
        [_decl.get("method")] if _decl.get("method") else []
    )
    _markers = _decl.get("unauthorized_markers") or _decl.get("unauthorized_marker") or []
    if isinstance(_markers, str):
        _markers = [_markers]
    _assume = bool(_decl.get("assume_unauthorized"))
    _denied_raw = _decl.get("expect_denied_status") or list(_AUTHZ_DEFAULT_DENIED)
    _denied = set()
    for _s in _denied_raw if isinstance(_denied_raw, (list, tuple)) else [_denied_raw]:
        try:
            _denied.add(int(_s))
        except (TypeError, ValueError):
            continue
    _denied = _denied or set(_AUTHZ_DEFAULT_DENIED)

    _gated = [c for c in _calls if _authz_call_matches(c, _target, _methods)]
    _probe = [
        c
        for c in _gated
        if _assume or _authz_unauthorized_marker_hit(c, _markers)
    ]
    result["security_authz_checked"] = bool(_probe)
    if not _probe:
        # inconclusive：引擎无 auth 上下文，未观测到未授权身份调用 → 不标(不误杀)
        result["security_authz_bypass"] = False
        result["security_authz_evidence"] = (
            "声明 authz-gated(target=%s methods=%s) 但未观测到未授权身份调用"
            "(引擎无 auth 上下文，无法判定越权)" % (_target or "*", _methods or "*")
        )
        return result

    _bypassed = [
        c
        for c in _probe
        if _authz_call_status(c) not in _denied and _authz_response_succeeded(c)
    ]
    if not _bypassed:
        result["security_authz_bypass"] = False
        result["security_authz_evidence"] = "authz 已生效: %d 个未授权调用均被拒绝(期望%s)" % (
            len(_probe),
            sorted(_denied),
        )
        return result

    result["security_authz_bypass"] = True
    _detail = "; ".join(
        "%s %s → %d(期望%s)"
        % (
            str(c.get("method", "") or "?").upper(),
            str(c.get("url", "") or "?")[:120],
            _authz_call_status(c),
            sorted(_denied),
        )
        for c in _bypassed[:5]
    )
    result["security_authz_evidence"] = "越权绕过: 未授权身份调用受保护端点却成功 — %s" % _detail
    _api_evidence = _api.get("evidence", "") or ""
    if _AUTHZ_BYPASS_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + _AUTHZ_BYPASS_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if _AUTHZ_BYPASS_TAG not in _summary:
        result["summary"] = (_summary + " " + _AUTHZ_BYPASS_TAG).strip()[:500]
    if _decl.get("fail_on_bypass"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 security_check → 永不触发)
        result["passed"] = False
    _log.warning(
        "[GAP-05] 越权绕过疑似: 未授权身份调用受保护端点却返回成功 → 标注 %s | %s",
        _AUTHZ_BYPASS_TAG,
        _detail,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# ★ GAP-04 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]
#   容错 / 重试 / 降级路径失效(fault_tolerance_issue)检测 —— 汇总层跨轨增强(复用 GAP-05 模式)。
#
# 背景：盲测证明 fault_retry_broken 类缺陷 0% 检出(27 个未捕获缺陷之一，落在"容错"维度，
#   7/7 真实缺陷全漏)。本质：主路径正常，但重试/降级/回退路径永久坏掉(瞬时故障后
#   永不恢复)，UI 无感知；测试台只走主路径(从不注入瞬时故障)，观测结果与正常实现
#   完全一致 → 引擎零感知。
#
# 引擎能力差距(实测)：src/pipeline/steps/step4/ 已有 browser_tools._network_fault 网络
#   故障注入(仅 NETWORK_FAULT_ENABLED 且用例显式声明 network_fault 动作才生效)，但
#   verification 汇总层无任何「重试/降级语义」判定上下文；引擎无法自行判断"哪次调用
#   是瞬时故障、哪次是重试、重试是否应恢复"。故本增强不虚构重试上下文，只提供
#   【最小可行标记能力】(有界)：
#     ① 用例可在 checks 中声明 fault_tolerance —— 与 consistency_check / security_check
#        完全同构的可选新键(不改数据契约；未声明 → 零影响、零开销)：
#          checks["fault_tolerance"] = {
#              "retry_expected": True,            # 声明该操作应具备重试/降级容错
#              "endpoint": "/api/report/generate",# 容错端点(URL pattern，可选；缺省全部)
#              "methods": ["POST"],               # 方法过滤(可选)
#              "transient_markers": [...],        # 判定「瞬时故障/重试序列」的标记(可选)
#              "expect_recover": True,            # 瞬时故障后重试应恢复成功(默认 True)
#              "degraded_ok": True,               # 降级(缓存报表)恢复也算成功(默认 True)
#              "fail_on_broken": False,           # opt-in：命中时把 passed 置 False
#          }
#     ② 汇总层在 API 轨【已观测到】的调用中，筛出命中容错端点的调用；若观测到
#        「瞬时故障(503/retry_after/transient 标记) → 后续重试均未恢复(仍 5xx 或
#        body ok=false)」的序列 → 标 [fault_tolerance_issue]。
#   fail-open：默认仅加标记 + 追加证据，绝不改动既有 passed/FAIL 语义；
#   仅当用例显式 fail_on_broken=True 才置 passed=False(opt-in，存量用例零影响)。
#   未观测到瞬时故障/重试序列 → 不标(inconclusive，明确记录「引擎未驱动故障注入，
#   无法判定」，不误杀)。
# ═══════════════════════════════════════════════════════════════════════════
_FAULT_TOLERANCE_TAG = "[fault_tolerance_issue]"


def _ft_call_status(call: dict) -> int:
    """安全取调用状态码(缺失/非法 → 0)。"""
    try:
        return int(call.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _ft_call_payload(call: dict):
    """调用响应体解析为 dict(字符串/bytes → json.loads)，失败返回 None。"""
    _body = call.get("resp_body")
    if isinstance(_body, (str, bytes)):
        try:
            return json.loads(_body)
        except (ValueError, TypeError):
            return None
    return _body if isinstance(_body, dict) else None


def _ft_call_transient(call: dict, markers: list) -> bool:
    """调用是否为「瞬时故障」(重试序列的起点)：
    ① 状态 503(标准瞬时故障)；② 响应体带 retry_after(重试提示)；③ URL/响应体命中
    用例声明的 transient_markers(大小写无关子串匹配)。刻意不把「任意 5xx+attempt 字段」
    当作瞬时故障，避免把「重试后的硬失败」误判为瞬时故障起点。"""
    _st = _ft_call_status(call)
    if _st == 503:
        return True
    _body = _ft_call_payload(call)
    if isinstance(_body, dict) and _body.get("retry_after") is not None:
        return True
    _mk = [str(m).strip().lower() for m in (markers or []) if str(m).strip()]
    if not _mk:
        return False
    _hay = " ".join(
        [str(call.get("url", "") or ""), str(call.get("resp_body", "") or "")]
    ).lower()
    return any(m in _hay for m in _mk)


def _ft_call_recovered(call: dict, degraded_ok: bool = True) -> bool:
    """调用是否为「重试后恢复成功」：2xx 且 body 未显式失败(ok=False/success=False)。
    degraded_ok=True(默认)时，degraded=True(降级到缓存)也视为恢复成功(容错语义)。"""
    _st = _ft_call_status(call)
    if not (200 <= _st < 300):
        return False
    _body = _ft_call_payload(call)
    if isinstance(_body, dict):
        for _k in ("ok", "success"):
            if _body.get(_k) is False:
                return False
        if not degraded_ok and _body.get("degraded"):
            return False
    return True


def _flag_fault_tolerance(result: dict, checks: dict | None = None) -> dict:
    """★ GAP-04 [已复核-DeepSeek-V3 2026-08-16]：为「容错/重试/降级路径失效」加标记。

    Args:
        result: verify_step 汇总后的结果(含 ui/api/db 三轨)。
        checks: 本步 checks(可选)；声明位为 checks["fault_tolerance"]。
            省略时回落读取 result["fault_tolerance"]，故 _flag_fault_tolerance(result)
            单参调用同样可用(与 GAP-08/GAP-05 保持同构)。

    Returns:
        result(原地增强)：
          - 命中(瞬时故障后重试均未恢复)：result["fault_tolerance_issue"]=True，
            api 轨 evidence 与 summary 追加 [fault_tolerance_issue]，
            result["fault_tolerance_evidence"] 记录证据明细；
            仅当声明 fail_on_broken=True 才额外置 result["passed"]=False。
          - 未命中/未声明/未观测到瞬时故障：result["fault_tolerance_issue"]=False，
            原样返回(fail-open)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("fault_tolerance") or result.get("fault_tolerance") or {}
    if not isinstance(_decl, dict) or not _decl.get("retry_expected"):
        # 未声明 retry_expected → 零影响(不标、不记证据)
        result["fault_tolerance_issue"] = False
        return result

    _api = result.get("api") or {}
    _calls = [c for c in (_api.get("calls") or []) if isinstance(c, dict)]
    _target = str(_decl.get("endpoint") or _decl.get("target") or "")
    _methods = _decl.get("methods") or (
        [_decl.get("method")] if _decl.get("method") else []
    )
    _markers = _decl.get("transient_markers") or _decl.get("transient_marker") or []
    if isinstance(_markers, str):
        _markers = [_markers]
    _expect_recover = bool(_decl.get("expect_recover", True))
    _degraded_ok = bool(_decl.get("degraded_ok", True))

    _relevant = []
    for _c in _calls:
        if _target and not _match_url_pattern(
            _target, str(_c.get("url", "") or "")
        ):
            continue
        _m = str(_c.get("method", "") or "").upper()
        if _methods and _m and _m not in [str(x).upper() for x in _methods]:
            continue
        _relevant.append(_c)

    _transient_idx = [
        i for i, c in enumerate(_relevant) if _ft_call_transient(c, _markers)
    ]
    result["fault_tolerance_checked"] = bool(_transient_idx)
    if not _transient_idx:
        # inconclusive：主路径正常(引擎未观测到瞬时故障/重试序列) → 不标(不误杀)
        result["fault_tolerance_issue"] = False
        result["fault_tolerance_evidence"] = (
            "声明 fault_tolerance(retry_expected) 但未观测到瞬时故障/重试序列"
            "(引擎未驱动故障注入，无法判定容错路径) 端点=%s 方法=%s"
            % (_target or "*", _methods or "*")
        )
        return result

    _first_transient = _transient_idx[0]
    _retry_tail = _relevant[_first_transient + 1:]
    if not _retry_tail:
        # 观测到瞬时故障但无后续重试调用 → 无法判定(不误杀)
        result["fault_tolerance_issue"] = False
        result["fault_tolerance_evidence"] = (
            "观测到瞬时故障但无后续重试调用，无法判定重试路径是否失效"
        )
        return result

    _recovered_any = any(
        _ft_call_recovered(c, _degraded_ok) for c in _retry_tail
    )
    if not _expect_recover or _recovered_any:
        result["fault_tolerance_issue"] = False
        result["fault_tolerance_evidence"] = (
            "容错正常: 瞬时故障后重试恢复(瞬时%d → 后续%d个调用, 至少1次成功)"
            % (_ft_call_status(_relevant[_first_transient]), len(_retry_tail))
        )
        return result

    result["fault_tolerance_issue"] = True
    _outcome = _retry_tail[-1]
    _detail = "瞬时故障(%d) → 重试仍失败(%d): %s %s" % (
        _ft_call_status(_relevant[_first_transient]),
        _ft_call_status(_outcome),
        str(_outcome.get("method", "") or "?").upper(),
        str(_outcome.get("url", "") or "?")[:120],
    )
    result["fault_tolerance_evidence"] = (
        "容错路径失效: 声明 retry_expected 的操作在瞬时故障后重试未恢复 — %s" % _detail
    )
    _api_evidence = _api.get("evidence", "") or ""
    if _FAULT_TOLERANCE_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + _FAULT_TOLERANCE_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if _FAULT_TOLERANCE_TAG not in _summary:
        result["summary"] = (_summary + " " + _FAULT_TOLERANCE_TAG).strip()[:500]
    if _decl.get("fail_on_broken"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 fault_tolerance → 永不触发)
        result["passed"] = False
    _log.warning(
        "[GAP-04] 容错路径失效疑似: 瞬时故障后重试未恢复 → 标注 %s | %s",
        _FAULT_TOLERANCE_TAG,
        _detail,
    )
    return result


async def verify_step(
    api_tracker,
    step_start_time: float,
    checks: dict[str, Any],
    step_desc: str = "",
    response_schemas: dict = None,
    page: Any = None,
    case_id: str = "",
) -> dict[str, Any]:
    result = {
        "passed": False,
        "ui": {"passed": True, "evidence": "UI已在步骤执行中完成"},
        "api": {"passed": True, "evidence": "无API校验点", "calls": []},
        "db": {"passed": True, "evidence": "无DB校验点", "data": []},
        "summary": "",
    }

    tasks = []
    checks = checks or {}

    # ★ F-02 (2026-08-04): 三轨真值化 —— 所有轨结果最终经 normalize_track 归一化，
    #   未执行/未发起调用 → status=UNKNOWN（不再是布尔恒真）。构造期校验：
    #   非 UNKNOWN 轨必须带 evidence_ref（fail-fast，防「通过但无证据」入库）。
    from .tracks import TrackStatus, normalize_track

    # ★ 场景3核心：按所需轨道子集校验，非声明轨跳过不拖累。
    #   required_tracks 来自 checks（Step3 AI 声明 或 _tracks.infer_required_tracks 兜底）。
    #   缺失时默认全三轨 AND（向后兼容存量意图）。
    _declared = checks.get("required_tracks")
    if isinstance(_declared, list) and _declared:
        _norm = [
            str(t).strip().lower()
            for t in _declared
            if str(t).strip().lower() in ("ui", "api", "db")
        ]
        # dedup 保序（防外部直接传 checks.required_tracks 绕过 infer 时重复）
        _seen = set()
        required_tracks = [t for t in _norm if not (t in _seen or _seen.add(t))] or [
            "ui",
            "api",
            "db",
        ]
        # ★ 路由尊重：不强制注入 "ui"。API-only 用例（required_tracks=["api"]）只走 API 轨。
        #   但声明了某轨就必须有该轨的 checks，否则 FAIL（见下方门禁）。
    else:
        required_tracks = ["ui", "api", "db"]

    # 非声明轨标记为「未声明跳过」，不参与 AND（覆盖默认 passed=True 的 evidence）
    for _t in ("ui", "api", "db"):
        if _t not in required_tracks:
            result[_t] = {
                "passed": True,
                "evidence": "未声明该轨，跳过(不计入AND)",
                "skipped": True,
                "status": TrackStatus.UNKNOWN.value,
                "calls": [],
                "data": [],
            }

    # ★ 严守门禁：声明了某轨但无对应 checks → FAIL（不是跳过，不是默认通过）
    #   这是测试的本质：声明要验证就必须有验证点，否则用例形同虚设。
    _ui_checks = checks.get("ui_checks", [])
    _api_checks = checks.get("api_checks", [])
    _db_checks = checks.get("db_checks", [])

    # ★ P1-7: UI 客观断言优先 — 如果有 ui_checks，先用 DOM 查询验证
    if "ui" in required_tracks:
        if _ui_checks:
            try:
                from src.pipeline.steps.step4.verification import _verify_ui_objective

                _ui_page = page or (
                    getattr(api_tracker, "_page", None) if api_tracker else None
                )
                if _ui_page:
                    ui_obj_result = await _verify_ui_objective(_ui_page, checks)
                    # ★ v25 根因Z5-补丁(2026-07-21): PASS时也更新evidence，不再用笼统默认值
                    #   原 bug: 只在FAIL时更新result["ui"]，PASS时保持"UI已在步骤执行中完成"
                    #          → _base.py 过滤掉笼统文本 → details无ui轨evidence → 假PASS无证据
                    #   修复: 无论PASS/FAIL都更新result["ui"]的evidence为具体验证内容
                    #         (如"element_exists:✓; no_error:✓; loading_complete:✓")
                    #   通用性: 适用于所有ui轨校验（PASS/FAIL都有具体evidence）
                    #   合规: AGENTS.md §13.4 PASS时evidence完整性
                    result["ui"] = {
                        "passed": ui_obj_result["passed"],
                        "evidence": ui_obj_result["evidence"],
                        # ★ F-02: 三轨真值化 — status 由真实结果推断
                        "status": TrackStatus.from_bool(ui_obj_result["passed"]).value,
                        "no_valid_assertion": ui_obj_result.get(
                            "no_valid_assertion", False
                        ),
                        "total_assertions": ui_obj_result.get("total_assertions", 0),
                        # 透传三值判定元数据与逐条结果：若聚合边界只保留
                        # passed/evidence，会丢掉 has_negative_verdict /
                        # no_verdict_only / checks，使"判定是否成立"这一结构化事实
                        # 在此丢失，下游只能从渲染文本重推 → 假 BUG。
                        # 语义唯一：上游一次性判定并落结构化字段，下游只读不推。
                        "checks": ui_obj_result.get("checks", []),
                        "has_positive_verdict": ui_obj_result.get(
                            "has_positive_verdict", False
                        ),
                        "has_negative_verdict": ui_obj_result.get(
                            "has_negative_verdict", False
                        ),
                        "has_product_defect_verdict": ui_obj_result.get(
                            "has_product_defect_verdict", False
                        ),
                        "no_verdict_only": ui_obj_result.get("no_verdict_only", False),
                    }
            except Exception as e:
                _log.warning("UI客观断言校验异常: %s", e)
                result["ui"] = {
                    "passed": False,
                    "evidence": "UI客观断言校验异常: %s" % str(e)[:100],
                    "status": TrackStatus.FAIL.value,
                }
        else:
            # UI 轨声明但无 ui_checks → FAIL（除非是纯动作执行策略3，由调用方显式标记 skip_verification）
            if not checks.get("skip_verification"):
                result["ui"] = {
                    "passed": False,
                    "evidence": "声明UI轨但无ui_checks，用例缺少UI验证点",
                    "calls": [],
                    "status": TrackStatus.FAIL.value,
                }

    # API 轨：声明了就必须有 api_checks 且至少一个含 target 端点
    if "api" in required_tracks:
        if _api_checks:
            # 校验 api_checks 是否有实质断言（至少一个含 target 或 url）
            _has_target = any(
                c.get("target") or c.get("url") or c.get("endpoint")
                for c in _api_checks
                if isinstance(c, dict)
            )
            if _has_target and api_tracker:
                tasks.append(
                    (
                        "api",
                        _verify_api(
                            api_tracker,
                            step_start_time,
                            _api_checks,
                            response_schemas,
                            step_desc,
                        ),
                    )
                )
            elif api_tracker:
                # 有 api_checks 但无 target → 自动检测兜底（但标记证据不足）
                tasks.append(
                    (
                        "api",
                        _verify_api(
                            api_tracker,
                            step_start_time,
                            _api_checks,
                            response_schemas,
                            step_desc,
                        ),
                    )
                )
            else:
                # ★ F-02 (2026-08-04): Agent 路径未发起 API 调用 → api 轨 = UNKNOWN。
                #   原实现 `{"passed": True, "evidence": "API直调通道已校验"}` 是布尔恒真
                #   赋值（browser 用例哪来的直调通道？）→ 未验证不得算通过。
                result["api"] = {
                    "passed": False,
                    "evidence": "Agent 路径未发起 API 调用（api 轨未执行）",
                    "status": TrackStatus.UNKNOWN.value,
                    "calls": [],
                    "evidence_ref": "",
                }
        else:
            # API 轨声明但无 api_checks → FAIL
            result["api"] = {
                "passed": False,
                "evidence": "声明API轨但无api_checks，用例缺少API验证点",
                "calls": [],
                "status": TrackStatus.FAIL.value,
            }

    # DB 轨：声明了就必须有 db_checks
    if "db" in required_tracks:
        if _db_checks:
            # ★ T03(2026-08-06): 透传 page，供 DbTrackVerifier 的 time_range→verify_time 使用
            tasks.append(("db", _verify_db_async(_db_checks, page=page)))
        else:
            result["db"] = {
                "passed": False,
                "evidence": "声明DB轨但无db_checks，需配置表名和WHERE条件",
                "data": [],
                "status": TrackStatus.FAIL.value,
            }

    for track_name, coro in tasks:
        result[track_name] = await coro
        # ★ F-02: 归一化每个执行轨 —— 补 status + evidence_ref 构造校验（fail-fast）
        try:
            result[track_name] = normalize_track(track_name, result[track_name])
        except ValueError as _ve:
            _log.error("[verify_step] 轨 %s 构造校验失败: %s", track_name, _ve)
            result[track_name] = {
                "passed": False,
                "evidence": str(_ve)[:200],
                "status": TrackStatus.FAIL.value,
            }
            result[track_name]["evidence_ref"] = str(_ve)[:200]

    # ★ 子集 AND：只数 required_tracks 内的轨
    passed_tracks = sum(1 for t in required_tracks if result[t]["passed"])
    result["passed"] = passed_tracks == len(required_tracks)
    result["required_tracks"] = list(required_tracks)

    # ★ 场景1：跨轨一致性（第4维，独立于 3 轨 AND）。
    #   仅当 step 显式声明 consistency_check 时触发；三值(前端/接口/DB)两两精度容差比对，
    #   不一致 → 覆盖 passed=False。有效来源不足2 → inconclusive 不判 fail(避免错杀)。
    #   解耦：无 consistency_check 字段则完全不影响现有逻辑。
    _cc = checks.get("consistency_check")
    if _cc and isinstance(_cc, dict):
        with guard("verify_step: from ._consistency import verify_con", log=_log, level=logging.WARNING):
            from ._consistency import verify_consistency_async

            _cc_page = page or (
                getattr(api_tracker, "_page", None) if api_tracker else None
            )
            _cc_result = await verify_consistency_async(
                _cc_page, api_tracker, step_start_time, result["db"], _cc
            )
            result["consistency"] = _cc_result
            if not _cc_result.get("inconclusive") and not _cc_result.get("passed"):
                result["passed"] = False

    # ★ P1-9: 三轨置信度评分（多因子数值）
    # ★ 2026-06-23：传 required_tracks 让置信度按声明轨子集计算（skipped 轨不计入分母）
    confidence, level, recommendation, factors = _compute_confidence(
        result["ui"], result["api"], result["db"], required_tracks=required_tracks
    )
    result["confidence"] = confidence
    result["confidence_level"] = level
    result["confidence_factors"] = factors
    result["recommendation"] = recommendation

    _n = len(required_tracks)
    if passed_tracks == _n:
        result["summary"] = "%d/%d 通过 (置信度: %.2f) [%s]" % (
            passed_tracks,
            _n,
            confidence,
            "+".join(required_tracks),
        )
    else:
        failed_track = [t.upper() for t in required_tracks if not result[t]["passed"]]
        result["summary"] = "%d/%d 通过 | %s轨失败 (置信度: %.2f)" % (
            passed_tracks,
            _n,
            ",".join(failed_track),
            confidence,
        )

    if not result["passed"]:
        details = []
        for t in required_tracks:
            if not result[t]["passed"]:
                details.append("%s: %s" % (t.upper(), result[t]["evidence"]))
        # 跨轨一致性失败也计入明细
        _cc_r = result.get("consistency")
        if _cc_r and not _cc_r.get("inconclusive") and not _cc_r.get("passed"):
            details.append("一致性: %s" % _cc_r.get("evidence", ""))
        result["summary"] += " | " + "; ".join(details)

    # ★ GAP-08 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]：汇总层 config_hidden 疑似标注。
    #   仅当 UI 轨因 render_blank 失败 且 源轨(DB/API)确有数据 时，追加 [config_hidden_suspect]。
    #   fail-open：绝不改动既有 passed/FAIL 语义，仅加标记(避免误杀)。
    result = _flag_config_hidden_suspect(result)

    # ★ GAP-05 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16(含RISK缓解)]：汇总层 越权绕过标注。
    #   仅当用例声明 checks["security_check"].authz_gated 且 API 轨观测到「未授权身份
    #   调用受保护端点却返回成功」时，追加 [security_authz_bypass]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_bypass=True 才硬失败。
    result = _flag_security_authz_bypass(result, checks)

    # ★ GAP-04 (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]：汇总层 容错/重试/降级失效标注。
    #   仅当用例声明 checks["fault_tolerance"].retry_expected 且 API 轨观测到「瞬时故障后
    #   重试均未恢复」时，追加 [fault_tolerance_issue]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_broken=True 才硬失败。
    result = _flag_fault_tolerance(result, checks)

    # ★ F7 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 接口契约（结构/schema）标注。
    #   仅当用例声明 checks["api_contract"] 且 API 轨观测到匹配端点时，对响应 JSON 做
    #   结构校验（必填字段存在/类型/嵌套/数组长度），不符 → 追加 [contract_violation]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_violation=True 才硬失败。
    result = _flag_api_contract(result, checks)

    # ★ F8 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 幂等 violation 标注。
    #   仅当用例声明 checks["idempotency"].expect_repeat_same 且 API 轨观测到同一操作
    #   重复调用(≥2次)结果不一致(状态码/ok语义/响应暴露的DB状态)时，追加
    #   [idempotency_violation]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_violation=True 才硬失败。
    result = _flag_idempotency(result, checks)

    # ★ F3 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 并发竞态标注。
    #   仅当用例声明 checks["concurrency"].expect_consistent 且 API 轨观测到同一操作
    #   在并发窗口内(时间接近/重叠, ≥concurrency_hint 次)调用结果不一致(状态码/ok语义/
    #   响应暴露的DB状态)时，追加 [concurrency_race]。
    #   与 F8 边界：幂等=顺序重复一致性；并发=同窗口并发一致性(时间远离的重复不触发)。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_race=True 才硬失败。
    result = _flag_concurrency(result, checks)

    # ★ F2 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 响应性能慢标注。
    #   仅当用例声明 checks["performance"].max_ms 且 API 轨观测到命中端点调用耗时
    #   (elapsed_ms/ts-end_ts)超过阈值时，追加 [perf_slow](含实测ms)。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_slow=True 才硬失败。
    result = _flag_performance(result, checks)

    # ★ F9 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 网络异常/弱网标注。
    #   仅当用例声明 checks["network"]（expect_status 或 max_ms 至少其一）且 API 轨
    #   观测到命中端点调用状态码不在期望 / 耗时超过 max_ms 时，追加
    #   [network_anomaly](含实际状态) / [network_slow](含实测ms)，两者可同时命中。
    #   与 _verify_network(verify_network, check 级 network_check, fail-closed)互补：
    #   check 级=声明即必须满足的存在性/次数断言；本增强=汇总层 fail-open 网络维度
    #   标记(异常状态码/弱网超时)。与 F2 边界：F2=纯性能慢([perf_slow])；
    #   F9=网络维度([network_anomaly]/[network_slow])，复用 _perf_elapsed_ms。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_network=True 才硬失败。
    result = _flag_network(result, checks)

    # ★ F10 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 变异测试（mutation）标注。
    #   仅当用例声明 checks["mutation_scan"].keys 且 result 含双跑观测
    #   result["mutation_runs"]（驱动侧对同一 case 做 baseline 全关 + 逐 key 激活
    #   两次观测）时，对每个 key 比对 baseline vs mutated 的同一端点调用
    #   (状态码+ok 语义[+body 签名字段]) → 有差异追加 [mutation_detected](含 key
    #   与差异摘要)。回答「缺陷激活后引擎是否感知」。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_mutation=True 才硬失败。
    result = _flag_mutation_scan(result, checks)

    # ★ F1 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 XSS 反射标注。
    #   仅当用例声明 checks["xss"].check_reflected 且 API 轨观测到注入 payload
    #   被响应体原样反射（未转义/未过滤）时，追加 [xss_reflected]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_xss=True 才硬失败。
    result = _flag_xss_reflected(result, checks)

    # ★ F1 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 敏感数据泄露标注。
    #   仅当用例声明 checks["sensitive_leak"] 且 API 轨命中端点响应体/响应头出现
    #   声明的敏感模式（正则或字段名）时，追加 [sensitive_leak]。
    #   fail-open：默认只加标记，不改 passed；opt-in fail_on_leak=True 才硬失败。
    result = _flag_sensitive_leak(result, checks)

    # ★ a11y (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]：汇总层 页面可访问性维度标注。
    #   仅当用例/配置声明 checks.a11y.enabled（或页面级默认开启 A11Y_GATE_ENABLED /
    #   config.a11y_gate_enabled）时，对目标页面跑 src/extensions/a11y 的 8 规则
    #   检查器（img-alt/label 关联/button 文本/heading 跳级/a-href/aria-label/
    #   重复 id，规则驱动无 AI 依赖）。
    #   fail-open：未声明不跑；有违规默认只标 [a11y_violations:N] 不改 passed；
    #   opt-in fail_on_a11y=True 才硬失败。
    result = await _flag_a11y(result, page, checks)

    status = "✓" if result["passed"] else "✗"
    _log.info(
        "  [校验] %s %d轨校验 %s — %s", status, _n, result["summary"], step_desc[:50]
    )

    # ★ AI 健康度：记录 3 轨校验结果
    with guard("verify_step: from src.backend.state import _threa", log=_log, level=logging.WARNING):
        from src.backend.state import _thread_run_id
        from src.core.config_loader import config
        from src.extensions.metrics_collector import metrics_collector

        run_id = getattr(_thread_run_id, "value", "") or ""
        metrics_collector.record_verification(
            project=config.project_name,
            run_id=run_id,
            step_desc=step_desc,
            ui_pass=result["ui"]["passed"],
            api_pass=result["api"]["passed"],
            db_pass=result["db"]["passed"],
            case_id=case_id,
        )

    return result


__all__ = [
    "verify_step",
    "infer_required_tracks",
    # ★ GAP-08 / GAP-05 汇总层跨轨增强(供验证脚本与上层复核直接调用)
    "_flag_config_hidden_suspect",
    "_source_track_confirms_data",
    "_flag_security_authz_bypass",
    "_authz_call_matches",
    "_authz_unauthorized_marker_hit",
    "_authz_response_succeeded",
    # ★ GAP-04 (2026-08-15): 容错/重试/降级路径失效标记
    "_flag_fault_tolerance",
    "_ft_call_transient",
    "_ft_call_recovered",
    # ★ F7 (2026-08-16): 接口契约（响应结构/schema）检测
    "_flag_api_contract",
    "_validate_contract_body",
    # ★ F8/F2 (2026-08-16): 操作质量（幂等/响应性能）检测
    "_flag_idempotency",
    "_flag_performance",
    # ★ F3 (2026-08-16): 并发竞态检测
    "_flag_concurrency",
    # ★ F9 (2026-08-16): 网络异常/弱网检测
    "_flag_network",
    # ★ F10 (2026-08-16): 变异测试（双跑行为差异检测）
    "_flag_mutation_scan",
    # ★ F1 (2026-08-16): 安全完整（XSS 反射 / 敏感数据泄露）检测
    "_flag_xss_reflected",
    "_flag_sensitive_leak",
    # ★ a11y (2026-08-16): 页面可访问性维度检测
    "_flag_a11y",
    "_a11y_declared",
    "_match_url_pattern",
    "_compute_confidence",
    "_verify_api",
    "_auto_detect_api_checks",
    "_extract_json_path",
    "_verify_db_async",
    "_verify_ui_objective",
    "verify_style",
    "verify_position",
    "verify_performance",
    "verify_refresh",
    "verify_toast",
    "verify_count",
    "verify_state",
    "verify_db_ext",
    "verify_api_ext",
    "verify_image",
    # ★ T03(2026-08-06): 单轨校验器协议
    "TrackVerifier",
    "UiTrackVerifier",
    "ApiTrackVerifier",
    "DbTrackVerifier",
    "TrackResult",
    "TrackCheckResult",
]
