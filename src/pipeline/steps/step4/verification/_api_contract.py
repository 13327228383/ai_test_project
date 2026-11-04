# -*- coding: utf-8 -*-
"""F7 接口契约（响应结构/schema）检测 —— 汇总层跨轨增强（fail-open）。

★ B 专项 F7 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]
   12 维度报告判为 P1 缺口：后端契约维度未覆盖。引擎三轨已有 api 轨
   （api_status_check 状态码 / api_response_body_check json path 值比较），
   缺的是【结构/schema 契约】——响应 JSON 的必填字段、字段类型、嵌套结构
   是否满足用例声明的契约。本模块在汇总层补上该能力（与 GAP-04/05/08 同构）。

与既有 API 检查的边界划分（不重复造轮子）：
  1. api_response_body_check（_verify_api_ext._check_response_body）＝「值级」比较：
     断言某字段的【值】（eq/gte/contains...），不关心结构是否存在/类型。
  2. api_contract_check（_verify_contract.verify_api_contract，fail-closed）＝「check 级」
     扁平契约：顶层 required_fields/types/enums/formats，url 子串匹配；声明即必须满足，
     无匹配请求/响应体为空 → FAIL（不允许假通过）。
  3. 本模块 _flag_api_contract（fail-open）＝「汇总层」结构契约：
     - 声明位 checks["api_contract"]（与 checks["security_check"] / checks["fault_tolerance"] 同构）；
     - 支持嵌套路径（data.items[0].id）与数组结构（array_ok / min_items / max_items）；
     - 默认只加 [contract_violation] 标记 + 证据，不改 passed；opt-in fail_on_violation=True 才硬失败；
     - 未声明 / 响应非 JSON / 端点未观测到 → 零影响、不误杀。

复用：_type_name（_verify_contract，同一类型映射语义）、_match_url_pattern（_url_match）、
     路径语法与 _api._extract_json_path 的 data.items[0].id 一致（但区分「缺失」与「null」）。
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any

from ._url_match import _match_url_pattern
from ._verify_contract import _type_name

_log = logging.getLogger(__name__)

CONTRACT_VIOLATION_TAG = "[contract_violation]"


def _resolve_path(data: Any, path: str) -> tuple[bool, Any]:
    """按点号+数组索引路径取 JSON 值，返回 (exists, value)。

    exists=False 表示路径不存在（与「存在但值为 null」区分）。
    路径语法与 _api._extract_json_path 一致：data.items[0].id。
    """
    if not path:
        return (True, data)
    if not isinstance(data, (dict, list)):
        return (False, None)
    parts = _re.split(r"\.|\[(.*?)\]", path)
    parts = [p for p in parts if p is not None and p != ""]
    current = data
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return (False, None)
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except (TypeError, ValueError):
                return (False, None)
            if not (0 <= idx < len(current)):
                return (False, None)
            current = current[idx]
        else:
            return (False, None)
    return (True, current)


def _norm_type(expected: Any) -> str:
    """契约类型别名归一：integer/int/long→number；bool→boolean；大小写不敏感。"""
    e = str(expected or "").strip().lower()
    if e in ("integer", "int", "long"):
        return "number"
    if e in ("bool",):
        return "boolean"
    return e


def _check_one_item(item: Any, required_fields: list, nested_paths: dict) -> list:
    """对单个响应元素（object）做结构校验，返回问题列表。

    required_fields: [{path, type}] —— 必填字段存在性 + 类型。
    nested_paths:    {path: {type, min_items, max_items}} —— 嵌套结构/数组长度/层级。
    """
    problems: list = []
    for rf in required_fields or []:
        if not isinstance(rf, dict):
            continue
        path = str(rf.get("path", "") or "")
        exp_type = _norm_type(rf.get("type", ""))
        if not path:
            continue
        exists, val = _resolve_path(item, path)
        if not exists:
            problems.append("必填字段缺失: %s" % path)
            continue
        if exp_type and _type_name(val) != exp_type:
            problems.append(
                "类型不符: %s 期望%s 实得%s" % (path, exp_type, _type_name(val))
            )
    for path, spec in (nested_paths or {}).items():
        if not isinstance(spec, dict) or not path:
            continue
        exists, val = _resolve_path(item, path)
        if not exists:
            problems.append("嵌套字段缺失: %s" % path)
            continue
        exp_type = _norm_type(spec.get("type", ""))
        if exp_type and _type_name(val) != exp_type:
            problems.append(
                "嵌套类型不符: %s 期望%s 实得%s"
                % (path, exp_type, _type_name(val))
            )
        if isinstance(val, list):
            min_items = spec.get("min_items")
            max_items = spec.get("max_items")
            if min_items is not None:
                try:
                    if len(val) < int(min_items):
                        problems.append(
                            "数组长度不符: %s 期望>=%s 实得%d"
                            % (path, min_items, len(val))
                        )
                except (TypeError, ValueError) as _e:
                    _log.debug("_check_one_item best-effort 容忍: %s", str(_e)[:120])
            if max_items is not None:
                try:
                    if len(val) > int(max_items):
                        problems.append(
                            "数组长度不符: %s 期望<=%s 实得%d"
                            % (path, max_items, len(val))
                        )
                except (TypeError, ValueError) as _e:
                    _log.debug("_check_one_item best-effort 容忍: %s", str(_e)[:120])
    return problems


def _normalize_required_fields(raw: Any) -> list:
    """required_fields 归一为 [{path, type}]（兼容 dict 形态 {path: type}）。"""
    if isinstance(raw, dict):
        return [{"path": k, "type": v} for k, v in raw.items()]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _normalize_nested_paths(raw: Any) -> dict:
    """nested_paths 归一为 {path: {type, min_items, max_items}}（兼容 list 形态）。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        return {
            str(p.get("path", "") or ""): p
            for p in raw
            if isinstance(p, dict) and p.get("path")
        }
    return {}


def _validate_contract_body(body: Any, decl: dict) -> tuple[bool, list, bool]:
    """校验单个响应体是否符合声明契约。

    返回 (passed, problems, structural_checked)：
      structural_checked=False 表示响应非 JSON 对象/数组，或声明无结构约束
      （降级不误杀——无法做结构校验时视为无结论，不标 violation）。
    """
    required_fields = _normalize_required_fields(decl.get("required_fields"))
    nested_paths = _normalize_nested_paths(decl.get("nested_paths"))
    array_ok = bool(decl.get("array_ok"))
    min_items = decl.get("min_items")
    max_items = decl.get("max_items")

    if not required_fields and not nested_paths:
        return True, [], False  # 无结构约束 → 跳过

    if isinstance(body, list):
        if not array_ok:
            return False, ["顶层结构不符: 期望object 实得array(未声明 array_ok)"], True
        if min_items is not None:
            try:
                if len(body) < int(min_items):
                    return False, [
                        "数组长度不符: 期望>=%s 实得%d" % (min_items, len(body))
                    ], True
            except (TypeError, ValueError) as _e:
                _log.debug("_validate_contract_body best-effort 容忍: %s", str(_e)[:120])
        if max_items is not None:
            try:
                if len(body) > int(max_items):
                    return False, [
                        "数组长度不符: 期望<=%s 实得%d" % (max_items, len(body))
                    ], True
            except (TypeError, ValueError) as _e:
                _log.debug("_validate_contract_body best-effort 容忍: %s", str(_e)[:120])
        problems: list = []
        for i, item in enumerate(body[:5]):  # 最多核 5 个元素防爆炸
            item_problems = _check_one_item(item, required_fields, nested_paths)
            if item_problems:
                problems.extend("[%d]%s" % (i, p) for p in item_problems)
                break
        return (len(problems) == 0), problems, True

    if isinstance(body, dict):
        problems = _check_one_item(body, required_fields, nested_paths)
        return (len(problems) == 0), problems, True

    # 非 JSON 对象/数组（字符串/数字/null）→ 降级不误杀
    return True, [], False


def _flag_api_contract(result: dict, checks: dict | None = None) -> dict:
    """★ F7 [已复核-DeepSeek-V3 2026-08-16]：接口契约（响应结构/schema）violation 标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["api_contract"]。
            省略时回落读取 result["api_contract"]，故 _flag_api_contract(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04 保持同构)。

    Returns:
        result(原地增强)：
          - 命中 violation：result["contract_violation"]=True，api 轨 evidence 与 summary
            追加 [contract_violation]，result["contract_violation_evidence"] 记录明细；
            仅当声明 fail_on_violation=True 才额外置 result["passed"]=False。
          - 契约通过：result["contract_violation"]=False，contract_checked=True，
            contract_violation_evidence 记录「契约通过」。
          - 未声明/未命中端点/响应非 JSON/无结构约束：contract_violation=False，
            原样返回(fail-open，不误杀)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("api_contract") or result.get("api_contract") or {}
    if not isinstance(_decl, dict) or not _decl:
        # 未声明 api_contract → 零影响（不标、不记证据）
        result["contract_violation"] = False
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

    result["contract_checked"] = bool(_matched)
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["contract_violation"] = False
        result["contract_violation_evidence"] = (
            "声明 api_contract(端点=%s 方法=%s) 但未观测到匹配 API 调用，无法校验结构"
            % (_target or "*", _mm or "*")
        )
        return result

    _call = _matched[-1]
    _body = _call.get("resp_body") or _call.get("response_body")
    if isinstance(_body, (str, bytes)):
        try:
            _body = json.loads(_body)
        except (ValueError, TypeError):
            _body = None
    if not isinstance(_body, (dict, list)):
        # 响应非 JSON 对象/数组 → 降级不误杀
        result["contract_violation"] = False
        result["contract_checked"] = False
        result["contract_violation_evidence"] = (
            "契约端点命中但响应非 JSON 对象/数组，无法做结构校验（降级不误杀）: %s %s"
            % (
                str(_call.get("method", "") or "?").upper(),
                str(_call.get("url", "") or "?")[:120],
            )
        )
        return result

    _passed, _problems, _checked = _validate_contract_body(_body, _decl)
    if not _checked:
        result["contract_violation"] = False
        result["contract_checked"] = False
        result["contract_violation_evidence"] = (
            "声明 api_contract 但无 required_fields/nested_paths 结构约束，跳过（不误杀）"
        )
        return result

    _method = str(_call.get("method", "") or "?").upper()
    _url = str(_call.get("url", "") or "?")[:120]
    if _passed:
        result["contract_violation"] = False
        result["contract_checked"] = True
        _n_req = len(_normalize_required_fields(_decl.get("required_fields")))
        _n_nest = len(_normalize_nested_paths(_decl.get("nested_paths")))
        result["contract_violation_evidence"] = (
            "契约通过: %s %s 响应结构符合声明(必填%d字段/类型/嵌套/数组)"
            % (_method, _url, _n_req + _n_nest)
        )
        return result

    result["contract_violation"] = True
    _detail = "; ".join(_problems[:8])
    result["contract_violation_evidence"] = (
        "契约不符: %s %s — %s" % (_method, _url, _detail)
    )
    _api_evidence = _api.get("evidence", "") or ""
    if CONTRACT_VIOLATION_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + CONTRACT_VIOLATION_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if CONTRACT_VIOLATION_TAG not in _summary:
        result["summary"] = (_summary + " " + CONTRACT_VIOLATION_TAG).strip()[:500]
    if _decl.get("fail_on_violation"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 api_contract → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F7] 接口契约不符 → 标注 %s | %s", CONTRACT_VIOLATION_TAG, _detail
    )
    return result


__all__ = [
    "_flag_api_contract",
    "_validate_contract_body",
    "_resolve_path",
    "CONTRACT_VIOLATION_TAG",
]
