# -*- coding: utf-8 -*-
"""F1 安全完整（XSS 反射 + 敏感数据泄露）—— 汇总层跨轨增强（fail-open）。

★ B 专项 F1 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]
   12 维度报告判为 P1 缺口：PRD F1「安全」维度要求 越权/XSS/敏感泄露 三面覆盖，
   既有 GAP-05（__init__._flag_security_authz_bypass）只做了「越权绕过」一角。
   本模块在汇总层补上另外两角（与 GAP-04/05/08、F7、F8 同构：声明 + 观测，fail-open）：
     - F1a checks["xss"]           → 反射型 XSS 检测（注入 payload 被响应体原样反射）
     - F1b checks["sensitive_leak"]→ 敏感数据泄露检测（响应体/响应头出现声明敏感模式）

与既有安全能力边界（不重复造轮子）：
  1. GAP-05 _flag_security_authz_bypass = 越权：声明 security_check.authz_gated，
     观测「未授权身份调用受保护端点却成功」→ [security_authz_bypass]。
     本模块 F1a/F1b = XSS/泄露，与 GAP-05 互补（同一 checks["security_check"] 的
     另外两角；若用例分开声明 xss / sensitive_leak 亦可独立生效）。
  2. src/extensions/security_scanner（SecurityScanner）＝【常开全局】静态扫描器：
     对所有响应做正则匹配（<script>alert(、身份证/手机号/邮箱、安全头缺失等），
     发现即记 finding 并降置信度，但【不可由用例声明端点/方法/模式】，也不区分
     「注入 payload 是否原样反射」。本模块 F1a/F1b 是【用例可声明、fail-open、
     端点/方法可过滤、默认不降级 passed】的汇总层标记能力——两者互补：
     Scanner 是通用兜底静态扫描，本模块是声明驱动的最小可行标记。

fail-open 语义（与 F7/F8 一致）：
  - 未声明 checks["xss"] / checks["sensitive_leak"] → 零影响（不标、不记证据）。
  - 默认仅加标记 + 追加证据，绝不改动既有 passed/FAIL 语义；
    仅当用例显式 fail_on_xss / fail_on_leak=True 才置 passed=False（opt-in）。
  - 端点未观测到 / payload 无法提取 / 响应无内容 → 不标（inconclusive，不误杀）。

行数说明（>120 行）：本模块实现两个独立检测（XSS 反射 + 敏感泄露），各自含
  公共小工具/归一化/扫描/标注流程，且附 fail-open 全分支（未声明/未命中/降级），
  参照 F7（_api_contract.py，345 行）与 F8（_op_quality.py，421 行）同构的
  完整文档 + 分支密度，故行数相当属预期。
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ._url_match import _match_url_pattern

_log = logging.getLogger(__name__)

XSS_REFLECTED_TAG = "[xss_reflected]"
SENSITIVE_LEAK_TAG = "[sensitive_leak]"


# ═══════════════════════════════════════════════════════════════════════════
# F1a XSS 反射：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
def _xss_query_params(url: str) -> dict:
    """从 URL 中提取 query 参数（首值），失败返回空 dict。"""
    if not url:
        return {}
    try:
        parsed = urlparse(str(url))
        return {
            k: v[0] if v else ""
            for k, v in parse_qs(parsed.query).items()
        }
    except Exception:
        return {}


def _xss_deep_get(data: Any, key: str):
    """递归在 dict/list 中按 key 取值（支持 post_data/req_body 嵌套 JSON）。"""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            r = _xss_deep_get(v, key)
            if r is not None:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _xss_deep_get(item, key)
            if r is not None:
                return r
    return None


def _xss_extract_payload(call: dict, param: str) -> str:
    """从一次 API 调用中提取「注入 payload」：优先 URL query，其次请求体。

    返回空字符串表示无法提取（不判定，fail-open）。
    """
    _param = str(param or "").strip()
    if not _param:
        return ""
    # 1) URL query
    _url = str(call.get("url", "") or "")
    _q = _xss_query_params(_url)
    if _param in _q and _q[_param] not in (None, ""):
        return str(_q[_param])
    # 2) 请求体（post_data / req_body / request_body）
    for _k in ("post_data", "req_body", "request_body"):
        _v = call.get(_k)
        if isinstance(_v, (dict, list)):
            _found = _xss_deep_get(_v, _param)
            if _found is not None and str(_found) != "":
                return str(_found)
        elif isinstance(_v, str) and _v.strip():
            _form = _xss_query_params("?" + _v.lstrip("?"))
            if _param in _form and _form[_param] not in (None, ""):
                return str(_form[_param])
    return ""


def _xss_response_text(call: dict) -> str:
    """响应体转纯文本：dict/list → json 序列化；str/bytes 原样。"""
    _body = call.get("resp_body") or call.get("response_body")
    if isinstance(_body, (dict, list)):
        return json.dumps(_body, ensure_ascii=False)
    if isinstance(_body, bytes):
        return _body.decode("utf-8", errors="replace")
    return str(_body or "")


def _xss_escaped_hit(text: str, payload: str) -> bool:
    """响应中是否出现「被转义」的 payload（仅作证据，不参与判定）。

    覆盖 HTML 实体转义（&lt;script&gt;）与 URL 编码（%3Cscript%3E）两种常见形态；
    用于负例时说明「有反射但被转义」，避免误判为无反射。
    """
    if not text or not payload:
        return False
    _esc = (
        payload.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    _url_enc = (
        payload.replace("<", "%3C").replace(">", "%3E").replace('"', "%22")
    )
    return (_esc in text) or (_url_enc in text)


def _flag_xss_reflected(result: dict, checks: dict | None = None) -> dict:
    """★ F1a [已复核-DeepSeek-V3 2026-08-16]：反射型 XSS 标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["xss"]。
            省略时回落读取 result["xss"]，故 _flag_xss_reflected(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7/F8 保持同构)。

    声明形态 checks["xss"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["GET"],                  # 方法过滤(可选)
            "param": "q",                        # 注入参数名（从 URL query/请求体提取）
            "payload": "<script>alert(1)</script>",  # 可选：直接声明注入 payload
            "check_reflected": true,             # 声明检查「原样反射」
            "fail_on_xss": false,                # opt-in：命中时把 passed 置 False
        }

    判定（用例驱动 + 观测，fail-open）：
      对命中端点/方法的每次调用，提取注入 payload（param 或声明 payload），
      若响应体【原样】包含该 payload（未转义/未过滤：原始 <script> 存在）
      → 标 [xss_reflected]；响应已转义（&lt;script&gt;）或无反射 → 不标。

    Returns:
        result(原地增强)：命中 → xss_reflected=True + 证据 + 标记（opt-in 硬失败）；
            未命中/未声明/无法提取 payload/端点未观测到 → False 原样返回。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("xss") or result.get("xss") or {}
    if not isinstance(_decl, dict) or not _decl.get("check_reflected"):
        # 未声明 check_reflected → 零影响（不标、不记证据）
        result["xss_reflected"] = False
        return result

    _api = result.get("api") or {}
    _calls = [c for c in (_api.get("calls") or []) if isinstance(c, dict)]
    _target = str(
        _decl.get("endpoint")
        or _decl.get("url_pattern")
        or _decl.get("target")
        or ""
    )
    _methods = _decl.get("methods") or (
        [_decl.get("method")] if _decl.get("method") else []
    )
    _mm = [str(m).strip().upper() for m in _methods if str(m).strip()]
    _param = str(_decl.get("param") or "")
    _declared_payload = str(_decl.get("payload") or "")

    _matched = []
    for _c in _calls:
        if _target and not _match_url_pattern(
            _target, str(_c.get("url", "") or "")
        ):
            continue
        _m = str(_c.get("method", "") or "").upper()
        if _mm and _m and _m not in _mm:
            continue
        _matched.append(_c)

    result["xss_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["xss_reflected"] = False
        result["xss_evidence"] = (
            "声明 xss(端点=%s 方法=%s) 但未观测到匹配 API 调用，无法检测反射"
            % (_target or "*", _mm or "*")
        )
        return result

    _reflected = []
    for _c in _matched:
        _payload = _declared_payload or _xss_extract_payload(_c, _param)
        if not _payload:
            continue
        if _payload in _xss_response_text(_c):
            _reflected.append((_c, _payload))

    result["xss_checked"] = bool(_matched)
    if not _reflected:
        # 负例：无原样反射。区分「转义」与「无反射」做证据说明，均不标
        result["xss_reflected"] = False
        _escaped = []
        for _c in _matched:
            _payload = _declared_payload or _xss_extract_payload(_c, _param)
            if _payload and _xss_escaped_hit(_xss_response_text(_c), _payload):
                _escaped.append(_payload[:40])
        if _escaped:
            result["xss_evidence"] = (
                "XSS 检测: payload 被转义(未原样反射)，%d 个命中调用均有转义 — %s"
                % (len(_matched), "; ".join(_escaped[:3]))
            )
        else:
            result["xss_evidence"] = (
                "XSS 检测: %d 个命中调用均未发现原样反射(payload=%s)"
                % (len(_matched), _declared_payload or ("param=" + _param))
            )
        return result

    _c, _payload = _reflected[0]
    result["xss_reflected"] = True
    _detail = "%s %s 原样反射 payload=%s" % (
        str(_c.get("method", "") or "?").upper(),
        str(_c.get("url", "") or "?")[:120],
        _payload[:80],
    )
    result["xss_evidence"] = "XSS 反射: %s" % _detail
    _api_evidence = _api.get("evidence", "") or ""
    if XSS_REFLECTED_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + XSS_REFLECTED_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if XSS_REFLECTED_TAG not in _summary:
        result["summary"] = (_summary + " " + XSS_REFLECTED_TAG).strip()[:500]
    if _decl.get("fail_on_xss"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 xss → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F1a] XSS 反射 → 标注 %s | %s", XSS_REFLECTED_TAG, _detail
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# F1b 敏感数据泄露：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
def _normalize_sensitive_patterns(decl: dict) -> tuple:
    """把声明归一为 (regexes, fields)。

    regexes: [(name, compiled_pattern), ...] —— 正则模式（对响应体/响应头做搜索）。
    fields:  [field_name, ...] —— JSON 字段名（对响应体 key 递归匹配）。

    兼容声明形态：
      patterns: [
          "身份证正则…",                     # 纯字符串 → 同时按正则与字段名解释
          {"name": "身份证", "regex": "…"},  # 显式正则
          {"field": "password"},             # 显式字段名
      ]
      regexes: ["…"], fields: ["password"]   # 便捷键（可选）
    """
    regexes: list = []
    fields: list = []

    def _add_regex(name: str, pat: str) -> None:
        if not pat:
            return
        try:
            regexes.append((name, _re.compile(pat, _re.IGNORECASE)))
        except _re.error:
            _log.debug("[sensitive_leak] 正则编译失败: %s", pat)

    _raw = decl.get("patterns") or []
    if isinstance(_raw, str):
        _raw = [_raw]
    for _p in _raw:
        if isinstance(_p, dict):
            _name = str(_p.get("name") or _p.get("label") or "")
            _rx = str(_p.get("regex") or _p.get("pattern") or "")
            if _rx:
                _add_regex(_name, _rx)
            elif _p.get("field"):
                fields.append(str(_p.get("field") or ""))
        else:
            _s = str(_p or "").strip()
            if _s:
                # 字符串同时按正则与字段名解释（身份证正则不会误命中 key，反之亦然）
                _add_regex(_s, _s)
                fields.append(_s)
    for _rx in decl.get("regexes") or []:
        _add_regex("", str(_rx or ""))
    for _f in decl.get("fields") or []:
        fields.append(str(_f or ""))
    return regexes, [f for f in fields if f]


def _sensitive_body_text(call: dict) -> str:
    """响应体转纯文本（用于正则搜索）：dict/list → json 序列化；str/bytes 原样。"""
    _body = call.get("resp_body") or call.get("response_body")
    if isinstance(_body, (dict, list)):
        return json.dumps(_body, ensure_ascii=False)
    if isinstance(_body, bytes):
        return _body.decode("utf-8", errors="replace")
    return str(_body or "")


def _sensitive_parsed_body(call: dict):
    """响应体解析为 dict/list（供字段名匹配）。

    兼容 api_tracker 已解析的 dict/list 与验证脚本/上层直传的 JSON 字符串两种形态；
    解析失败返回原始值（字段名匹配降级为不命中，不误杀）。
    """
    _body = call.get("resp_body") or call.get("response_body")
    if isinstance(_body, (dict, list)):
        return _body
    if isinstance(_body, bytes):
        try:
            return json.loads(_body.decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            return _body
    if isinstance(_body, str) and _body.strip():
        try:
            return json.loads(_body)
        except (ValueError, TypeError):
            return _body
    return _body


def _sensitive_field_hit(data: Any, field: str) -> bool:
    """JSON 响应体中是否出现名为 field 的字段（key 递归、大小写不敏感）。"""
    if not isinstance(data, (dict, list)) or not field:
        return False
    _f = str(field).lower()
    if isinstance(data, dict):
        for _k, _v in data.items():
            if str(_k).lower() == _f:
                return True
            if _sensitive_field_hit(_v, field):
                return True
    else:
        for _item in data:
            if _sensitive_field_hit(_item, field):
                return True
    return False


def _sensitive_headers(call: dict) -> dict:
    """取响应头 dict（缺失返回空 dict）。"""
    _h = call.get("resp_headers") or call.get("response_headers") or {}
    return _h if isinstance(_h, dict) else {}


def _flag_sensitive_leak(result: dict, checks: dict | None = None) -> dict:
    """★ F1b [已复核-DeepSeek-V3 2026-08-16]：敏感数据泄露标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["sensitive_leak"]。
            省略时回落读取 result["sensitive_leak"]，故 _flag_sensitive_leak(result)
            单参调用同样可用(与 F7/F8 保持同构)。

    声明形态 checks["sensitive_leak"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["GET"],                  # 方法过滤(可选)
            "mode": "forbid",                    # forbid=响应中禁止出现（默认）
            "patterns": [                        # 敏感模式（正则或字段名，见归一化）
                {"name": "身份证", "regex": "[1-9]\\d{5}(19|20)\\d{2}...\\d{3}[\\dXx]"},
                {"field": "password"},
                "1[3-9]\\d{9}",                  # 手机号
            ],
            "fail_on_leak": false,               # opt-in：命中时把 passed 置 False
        }

    判定（声明 + 观测，fail-open）：扫描命中端点/方法的响应体文本 + 响应头，
      命中声明的正则 或 JSON 字段名 → 标 [sensitive_leak]；无敏感内容 → 不标。

    Returns:
        result(原地增强)：命中 → sensitive_leak=True + 证据 + 标记（opt-in 硬失败）；
            未命中/未声明/无模式/端点未观测到 → False 原样返回(fail-open)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("sensitive_leak") or result.get("sensitive_leak") or {}
    if not isinstance(_decl, dict) or not _decl:
        # 未声明 sensitive_leak → 零影响（不标、不记证据）
        result["sensitive_leak"] = False
        return result

    _regexes, _fields = _normalize_sensitive_patterns(_decl)
    if not _regexes and not _fields:
        result["sensitive_leak"] = False
        result["sensitive_leak_checked"] = False
        result["sensitive_leak_evidence"] = (
            "声明 sensitive_leak 但无 patterns/regexes/fields 敏感模式，跳过（不误杀）"
        )
        return result

    _api = result.get("api") or {}
    _calls = [c for c in (_api.get("calls") or []) if isinstance(c, dict)]
    _target = str(
        _decl.get("endpoint")
        or _decl.get("url_pattern")
        or _decl.get("target")
        or ""
    )
    _methods = _decl.get("methods") or (
        [_decl.get("method")] if _decl.get("method") else []
    )
    _mm = [str(m).strip().upper() for m in _methods if str(m).strip()]

    _matched = []
    for _c in _calls:
        if _target and not _match_url_pattern(
            _target, str(_c.get("url", "") or "")
        ):
            continue
        _m = str(_c.get("method", "") or "").upper()
        if _mm and _m and _m not in _mm:
            continue
        _matched.append(_c)

    result["sensitive_leak_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["sensitive_leak"] = False
        result["sensitive_leak_evidence"] = (
            "声明 sensitive_leak(端点=%s 方法=%s) 但未观测到匹配 API 调用"
            % (_target or "*", _mm or "*")
        )
        return result

    _hits = []
    for _c in _matched:
        _text = _sensitive_body_text(_c)
        _headers = _sensitive_headers(_c)
        _headers_text = (
            json.dumps(_headers, ensure_ascii=False) if _headers else ""
        )
        for _name, _rx in _regexes:
            _m = _rx.search(_text) or _rx.search(_headers_text)
            if _m:
                _hits.append(
                    (_c, "regex", _name or _rx.pattern, _m.group(0)[:40])
                )
        _body = _sensitive_parsed_body(_c)
        for _f in _fields:
            if _sensitive_field_hit(_body, _f) or _sensitive_field_hit(
                _headers, _f
            ):
                _hits.append((_c, "field", _f, _f))

    result["sensitive_leak_checked"] = bool(_matched)
    if not _hits:
        result["sensitive_leak"] = False
        result["sensitive_leak_evidence"] = (
            "敏感泄露检测: %d 个命中调用未发现声明的敏感模式(正则%d 字段%d)"
            % (len(_matched), len(_regexes), len(_fields))
        )
        return result

    # 去重（同端点同模式只记一次），保留前 5 个命中做证据
    _seen = set()
    _uniq = []
    for _c, _kind, _name, _sample in _hits:
        _key = (
            str(_c.get("method", "") or "?").upper(),
            str(_c.get("url", "") or "?")[:120],
            _kind,
            str(_name),
        )
        if _key in _seen:
            continue
        _seen.add(_key)
        _uniq.append((_c, _kind, _name, _sample))
        if len(_uniq) >= 5:
            break

    _c, _kind, _name, _sample = _uniq[0]
    result["sensitive_leak"] = True
    _detail = "%s %s 响应命中敏感模式(%s: %s)" % (
        str(_c.get("method", "") or "?").upper(),
        str(_c.get("url", "") or "?")[:120],
        _kind,
        _name,
    )
    _extra = "; ".join(
        "%s:%s" % (k, n) for _, k, n, _ in _uniq[1:]
    )
    _evidence = "敏感数据泄露: %s | 样例=%s" % (_detail, _sample)
    if _extra:
        _evidence += " | 其他命中: %s" % _extra
    result["sensitive_leak_evidence"] = _evidence
    _api_evidence = _api.get("evidence", "") or ""
    if SENSITIVE_LEAK_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + SENSITIVE_LEAK_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if SENSITIVE_LEAK_TAG not in _summary:
        result["summary"] = (_summary + " " + SENSITIVE_LEAK_TAG).strip()[:500]
    if _decl.get("fail_on_leak"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 sensitive_leak → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F1b] 敏感泄露 → 标注 %s | %s", SENSITIVE_LEAK_TAG, _detail
    )
    return result


__all__ = [
    "_flag_xss_reflected",
    "_xss_extract_payload",
    "_flag_sensitive_leak",
    "_normalize_sensitive_patterns",
    "_sensitive_field_hit",
    "XSS_REFLECTED_TAG",
    "SENSITIVE_LEAK_TAG",
]
