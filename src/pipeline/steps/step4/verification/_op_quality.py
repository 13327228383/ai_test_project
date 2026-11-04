# -*- coding: utf-8 -*-
"""F8 幂等 / F2 响应性能 / F3 并发 / F9 网络 / F10 变异 —— 操作质量汇总层检测（fail-open）。

★ B 专项 F8 + F2 (2026-08-16) [已复核-DeepSeek-V3 2026-08-16]
   12 维度报告判为 P1 缺口：幂等性与响应性能维度未覆盖。本模块在汇总层补上
   两个【最小可行标记能力】（与 GAP-04/05/08、F7 同构：声明 + 观测，fail-open）。
★ F3 (2026-08-16)：并发竞态检测（复用 F8 helper）。
★ F9 (2026-08-16)：网络异常/弱网检测（复用 F2 _perf_elapsed_ms / F8 _op_call_status）。
★ F10 (2026-08-16)：变异测试（mutation）——盲靶变异激活 → baseline vs mutated 双跑
   行为差异（behavioral delta）检测（复用 F8 _op_call_outcome/_op_group_key/_op_call_status）。

与既有能力边界（不重复造轮子）：
  1. 幂等（F8）：引擎既有 API 轨只有「状态码 / 值级 json path」检查，每次调用独立
     判定，无法回答「同一操作重复调用结果是否一致」。_flag_idempotency 在汇总层
     按声明 checks["idempotency"] 对「同一操作(同 URL+method)」的重复调用(≥2次)
     比对 (状态码, body ok 语义[, 响应暴露的 DB 状态字段])，不一致
     → [idempotency_violation]。
  2. 性能（F2）：既有 _verify_perf._check_response_time 是【check 级】断言
     （response_time_check，fail-closed，声明即必须满足）；ApiTracker.on_response
     已记录每次调用的 elapsed_ms（W3 T05）。_flag_performance 是【汇总层】标记：
     按声明 checks["performance"] 对命中端点调用读 elapsed_ms（或 ts/end_ts 兜底），
     超 max_ms → [perf_slow]。复用 elapsed_ms 数据源，不重复计时。

fail-open 语义（两者一致）：未声明 / 无观测数据 → 不标、不记证据；
默认仅加标记，不改 passed；opt-in fail_on_violation / fail_on_slow=True 才硬失败。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ._url_match import _match_url_pattern
from ._api import _extract_json_path

_log = logging.getLogger(__name__)

IDEMPOTENCY_TAG = "[idempotency_violation]"
PERF_SLOW_TAG = "[perf_slow]"
# ★ F9 (2026-08-16): 网络异常/弱网标记
NETWORK_ANOMALY_TAG = "[network_anomaly]"
NETWORK_SLOW_TAG = "[network_slow]"
# ★ F10 (2026-08-16): 变异测试标记（盲靶变异激活 → 双跑行为差异被感知）
MUTATION_DETECTED_TAG = "[mutation_detected]"


# ═══════════════════════════════════════════════════════════════════════════
# F8 幂等：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
def _op_call_status(call: dict) -> int:
    """安全取调用状态码(缺失/非法 → 0)。"""
    try:
        return int(call.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _op_call_ok(call: dict) -> bool:
    """body ok 语义：响应 JSON 顶层 ok/success 显式 False → False，否则只看状态码。"""
    _body = call.get("resp_body") or call.get("response_body")
    if isinstance(_body, (str, bytes)):
        try:
            _body = json.loads(_body)
        except (ValueError, TypeError):
            return True
    if isinstance(_body, dict):
        for _k in ("ok", "success"):
            if _body.get(_k) is False:
                return False
    return True


def _op_call_outcome(call: dict) -> tuple:
    """调用结果状态签名：(状态码, body ok 语义)。"""
    return (_op_call_status(call), _op_call_ok(call))


def _op_outcome_str(outcome: tuple) -> str:
    """结果签名人类可读：200/ok=True。"""
    return "%d/ok=%s" % (outcome[0], outcome[1])


def _op_group_key(call: dict) -> str:
    """「同一操作」标识 = URL(去 query) + method 大写。"""
    _url = str(call.get("url", "") or "")
    _q = _url.find("?")
    _path = _url[:_q] if _q >= 0 else _url
    return "%s %s" % (str(call.get("method", "") or "?").upper(), _path)


def _op_duplicate_hit(call: dict, dup: dict) -> bool:
    """响应暴露的 DB 状态重复写入判定（可选声明 db_duplicate_check）。

    声明形态：{"count_path": "data.count", "expect_max": 1}
    语义：重复调用后，响应中暴露的 DB 行数(或计数)超过 expect_max → 重复写入。
    """
    if not isinstance(dup, dict) or not dup.get("count_path"):
        return False
    _val = _extract_json_path(call.get("resp_body"), str(dup["count_path"]))
    if not isinstance(_val, (int, float)):
        return False
    try:
        _max = float(dup.get("expect_max", 1))
    except (TypeError, ValueError):
        _max = 1.0
    return float(_val) > _max


def _flag_idempotency(result: dict, checks: dict | None = None) -> dict:
    """★ F8 [已复核-DeepSeek-V3 2026-08-16]：幂等 violation 标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["idempotency"]。
            省略时回落读取 result["idempotency"]，故 _flag_idempotency(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7 保持同构)。

    声明形态 checks["idempotency"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["POST"],                  # 方法过滤(可选)
            "expect_repeat_same": true,           # 声明该操作重复调用应结果一致
            "body_signature_fields": ["data.count"],  # 可选：跨调用必须一致的响应字段
            "db_duplicate_check": {"count_path": "data.count", "expect_max": 1},
                                                  # 可选：响应暴露的 DB 行数上限
            "fail_on_violation": false,           # opt-in：命中时把 passed 置 False
        }

    Returns:
        result(原地增强)：
          - 命中(同一操作重复调用结果不一致)：result["idempotency_violation"]=True，
            api 轨 evidence 与 summary 追加 [idempotency_violation]，
            result["idempotency_evidence"] 记录证据明细；
            仅当声明 fail_on_violation=True 才额外置 result["passed"]=False。
          - 一致/未声明/仅调用一次/端点未观测到：idempotency_violation=False，
            原样返回(fail-open，不误杀)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("idempotency") or result.get("idempotency") or {}
    if not isinstance(_decl, dict) or not _decl.get("expect_repeat_same"):
        # 未声明 expect_repeat_same → 零影响(不标、不记证据)
        result["idempotency_violation"] = False
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
    _sig_fields = _decl.get("body_signature_fields") or []
    if isinstance(_sig_fields, str):
        _sig_fields = [_sig_fields]
    _dup = _decl.get("db_duplicate_check") or {}
    if not isinstance(_dup, dict):
        _dup = {}

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

    result["idempotency_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["idempotency_violation"] = False
        result["idempotency_evidence"] = (
            "声明 idempotency(端点=%s 方法=%s) 但未观测到匹配 API 调用，无法判定幂等"
            % (_target or "*", _mm or "*")
        )
        return result

    # 按「同一操作」(URL+method) 分组
    _groups: dict = {}
    for _c in _matched:
        _groups.setdefault(_op_group_key(_c), []).append(_c)
    _repeat_keys = [k for k, v in _groups.items() if len(v) >= 2]
    result["idempotency_checked"] = bool(_repeat_keys)
    if not _repeat_keys:
        # 同一操作仅被调用 1 次 → 无法判定重复一致性（不误杀）
        result["idempotency_violation"] = False
        result["idempotency_evidence"] = (
            "声明 idempotency(expect_repeat_same) 但未观测到同一操作被调用 ≥2 次，"
            "无法判定幂等"
        )
        return result

    _violations = []
    for _k in _repeat_keys:
        _g = _groups[_k]
        _base = _g[0]
        _base_out = _op_call_outcome(_base)
        _base_sig = [
            _extract_json_path(_base.get("resp_body"), _f) for _f in _sig_fields
        ]
        for _i, _c in enumerate(_g[1:], start=2):
            _diff = []
            _out = _op_call_outcome(_c)
            if _out != _base_out:
                _diff.append(
                    "第1次(%s) vs 第%d次(%s)"
                    % (_op_outcome_str(_base_out), _i, _op_outcome_str(_out))
                )
            _sig = [
                _extract_json_path(_c.get("resp_body"), _f) for _f in _sig_fields
            ]
            if _sig_fields and _sig != _base_sig:
                _diff.append("body签名字段不一致(%s)" % ",".join(_sig_fields))
            if _op_duplicate_hit(_c, _dup):
                _diff.append(
                    "响应暴露DB状态重复写入(%s>%s)"
                    % (_dup.get("count_path"), _dup.get("expect_max"))
                )
            if _diff:
                _violations.append(
                    "%s %s — %s"
                    % (
                        str(_base.get("method", "") or "?").upper(),
                        str(_base.get("url", "") or "?")[:120],
                        "; ".join(_diff),
                    )
                )
                break

    if not _violations:
        result["idempotency_violation"] = False
        result["idempotency_evidence"] = (
            "幂等正常: %d 个重复操作组结果一致(状态码+ok语义%s)"
            % (len(_repeat_keys), "+body签名" if _sig_fields else "")
        )
        return result

    result["idempotency_violation"] = True
    _detail = "; ".join(_violations[:5])
    result["idempotency_evidence"] = (
        "幂等不符: 同一操作重复调用结果不一致 — %s" % _detail
    )
    _api_evidence = _api.get("evidence", "") or ""
    if IDEMPOTENCY_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + IDEMPOTENCY_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if IDEMPOTENCY_TAG not in _summary:
        result["summary"] = (_summary + " " + IDEMPOTENCY_TAG).strip()[:500]
    if _decl.get("fail_on_violation"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 idempotency → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F8] 幂等不符 → 标注 %s | %s", IDEMPOTENCY_TAG, _detail
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# F2 响应性能：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
def _perf_elapsed_ms(call: dict) -> float | None:
    """取调用耗时(ms)。

    优先读 ApiTracker.on_response 已记录的 elapsed_ms（W3 T05，与
    _verify_perf._check_response_time 同源）；兼容其它耗时字段名；
    再回落 ts/end_ts(秒) 时间戳差。无耗时数据 → None(fail-open)。
    """
    for _k in (
        "elapsed_ms",
        "duration_ms",
        "response_time_ms",
        "time_ms",
        "latency_ms",
    ):
        _v = call.get(_k)
        if _v is not None:
            try:
                return float(_v)
            except (TypeError, ValueError):
                continue
    _ts = call.get("ts")
    _end = call.get("end_ts") or call.get("ts_end") or call.get("completed_ts")
    if _ts is not None and _end is not None:
        try:
            return max(float(_end) - float(_ts), 0.0) * 1000.0
        except (TypeError, ValueError):
            return None
    return None


def _flag_performance(result: dict, checks: dict | None = None) -> dict:
    """★ F2 [已复核-DeepSeek-V3 2026-08-16]：响应性能慢标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["performance"]。
            省略时回落读取 result["performance"]，故 _flag_performance(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7 保持同构)。

    声明形态 checks["performance"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["POST"],                  # 方法过滤(可选)
            "max_ms": 1000,                       # 耗时阈值(ms)
            "fail_on_slow": false,                # opt-in：命中时把 passed 置 False
        }

    Returns:
        result(原地增强)：
          - 命中(命中端点调用耗时 > max_ms)：result["perf_slow"]=True，
            api 轨 evidence 与 summary 追加 [perf_slow](含实测ms)，
            result["perf_evidence"] 记录证据明细；
            仅当声明 fail_on_slow=True 才额外置 result["passed"]=False。
          - 未超阈值/未声明/无耗时数据/端点未观测到：perf_slow=False，
            原样返回(fail-open，不误杀)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("performance") or result.get("performance") or {}
    if not isinstance(_decl, dict) or not _decl:
        # 未声明 performance → 零影响(不标、不记证据)
        result["perf_slow"] = False
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
    try:
        _max_ms = float(_decl.get("max_ms") or 5000)
    except (TypeError, ValueError):
        _max_ms = 5000.0

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

    result["perf_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["perf_slow"] = False
        result["perf_evidence"] = (
            "声明 performance(端点=%s 方法=%s) 但未观测到匹配 API 调用"
            % (_target or "*", _mm or "*")
        )
        return result

    _with_time = [(c, _perf_elapsed_ms(c)) for c in _matched]
    _with_time = [(c, t) for c, t in _with_time if t is not None]
    result["perf_checked"] = bool(_with_time)
    if not _with_time:
        # 命中调用无耗时数据 → 降级不误杀
        result["perf_slow"] = False
        result["perf_evidence"] = (
            "声明 performance(max_ms=%d) 但命中调用无耗时数据(未记录 elapsed_ms)，"
            "无法判定" % int(_max_ms)
        )
        return result

    _slow = [(c, t) for c, t in _with_time if t > _max_ms]
    if not _slow:
        result["perf_slow"] = False
        result["perf_evidence"] = (
            "响应性能正常: %d 个命中调用耗时均未超阈值 max_ms=%d(最慢 %.0fms)"
            % (len(_with_time), int(_max_ms), max(t for _, t in _with_time))
        )
        return result

    _c, _t = max(_slow, key=lambda x: x[1])
    result["perf_slow"] = True
    _detail = "%s %s 实测 %.0fms 超过 max_ms=%d" % (
        str(_c.get("method", "") or "?").upper(),
        str(_c.get("url", "") or "?")[:120],
        _t,
        int(_max_ms),
    )
    result["perf_evidence"] = "响应性能慢: %s" % _detail
    _api_evidence = _api.get("evidence", "") or ""
    if PERF_SLOW_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + PERF_SLOW_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if PERF_SLOW_TAG not in _summary:
        result["summary"] = (_summary + " " + PERF_SLOW_TAG).strip()[:500]
    if _decl.get("fail_on_slow"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 performance → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F2] 响应性能慢 → 标注 %s | %s", PERF_SLOW_TAG, _detail
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# F3 并发检测：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
CONCURRENCY_TAG = "[concurrency_race]"
CONCURRENCY_DEFAULT_WINDOW_MS = 1500.0


def _op_call_ts(call: dict) -> float | None:
    """调用开始时间戳(秒)；缺失/非法 → None。"""
    try:
        return float(call.get("ts"))
    except (TypeError, ValueError):
        return None


def _op_call_end_ts(call: dict) -> float | None:
    """调用结束时间戳(秒)：优先 ts+elapsed_ms；无耗时数据 → ts。"""
    _ts = _op_call_ts(call)
    if _ts is None:
        return None
    _el = call.get("elapsed_ms")
    if _el is not None:
        try:
            return _ts + max(float(_el), 0.0) / 1000.0
        except (TypeError, ValueError) as _e:
            _log.debug("_op_call_end_ts best-effort 容忍: %s", str(_e)[:120])
    return _ts


def _op_concurrent_clusters(calls: list, window_ms: float) -> list:
    """把同一操作的一组调用按「并发窗口」聚类。

    并发窗口判定（满足其一即视为同窗口，与 F8 的任意重复区分）：
      ① 开始时间差 ≤ window_ms（接近并发，如两连发 POST）；
      ② 执行区间重叠（先发调用尚未结束，后发调用已开始 → 真并发）。

    返回按 ts 排序的簇列表，每簇 ≥2 个调用；无 ts 数据 → 返回空
    （不可证并发，fail-open 不误杀）。时间上远离的重复调用不会并入同一簇。
    """
    _timed = [(c, _op_call_ts(c)) for c in calls if _op_call_ts(c) is not None]
    if len(_timed) < 2:
        return []
    _timed.sort(key=lambda x: x[1])
    _tol = max(float(window_ms), 0.0) / 1000.0
    _clusters: list = []
    _cur = [_timed[0][0]]
    _cur_end = _op_call_end_ts(_timed[0][0]) or _timed[0][1]
    for _c, _ts in _timed[1:]:
        _end = _op_call_end_ts(_c) or _ts
        # 新调用开始时间在「当前簇末端 + 窗口容差」内 → 同窗口（接近/重叠）
        if _ts <= _cur_end + _tol:
            _cur.append(_c)
            _cur_end = max(_cur_end, _end)
        else:
            if len(_cur) >= 2:
                _clusters.append(_cur)
            _cur = [_c]
            _cur_end = _end
    if len(_cur) >= 2:
        _clusters.append(_cur)
    return _clusters


def _concurrency_compare_cluster(cluster: list, sig_fields: list, dup: dict) -> list:
    """比对并发窗口内各调用的结果一致性，返回差异原因列表(空=一致)。

    与 F8 _flag_idempotency 的比对逻辑同源：以簇内第 1 次调用为基线，
    逐次比对 (状态码, body ok 语义[, 签名字段][, 响应暴露的 DB 状态])。
    """
    _diffs: list = []
    _base = cluster[0]
    _base_out = _op_call_outcome(_base)
    _base_sig = [_extract_json_path(_base.get("resp_body"), _f) for _f in sig_fields]
    for _i, _c in enumerate(cluster[1:], start=2):
        _diff = []
        _out = _op_call_outcome(_c)
        if _out != _base_out:
            _diff.append(
                "第1次(%s) vs 第%d次(%s)"
                % (_op_outcome_str(_base_out), _i, _op_outcome_str(_out))
            )
        _sig = [_extract_json_path(_c.get("resp_body"), _f) for _f in sig_fields]
        if sig_fields and _sig != _base_sig:
            _diff.append("body签名字段不一致(%s)" % ",".join(sig_fields))
        if _op_duplicate_hit(_c, dup):
            _diff.append(
                "响应暴露DB状态重复写入(%s>%s)"
                % (dup.get("count_path"), dup.get("expect_max"))
            )
        if _diff:
            _diffs.append("; ".join(_diff))
    return _diffs


def _flag_concurrency(result: dict, checks: dict | None = None) -> dict:
    """★ F3 [已复核-DeepSeek-V3 2026-08-16]：并发竞态标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["concurrency"]。
            省略时回落读取 result["concurrency"]，故 _flag_concurrency(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7/F8 保持同构)。

    声明形态 checks["concurrency"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["POST"],                  # 方法过滤(可选)
            "expect_consistent": true,            # 声明并发调用应结果一致
            "concurrency_hint": 2,                # 并发窗口内至少 N 次调用才算并发观测(默认 2)
            "window_ms": 1500,                    # 并发窗口(ms)：开始时间差 ≤ 此值或区间重叠
            "body_signature_fields": ["data.count"],  # 可选：并发调用间必须一致的响应字段
            "db_duplicate_check": {"count_path": "data.count", "expect_max": 1},
                                                  # 可选：响应暴露的 DB 行数上限(重复写入)
            "fail_on_race": false,                # opt-in：命中时把 passed 置 False
        }

    Returns:
        result(原地增强)：
          - 命中(并发窗口内同一操作调用结果不一致/暴露竞态)：result["concurrency_race"]=True，
            api 轨 evidence 与 summary 追加 [concurrency_race]，
            result["concurrency_evidence"] 记录证据明细；
            仅当声明 fail_on_race=True 才额外置 result["passed"]=False。
          - 一致/未声明/单次/无并发窗口/端点未观测到：concurrency_race=False，
            原样返回(fail-open，不误杀)。

    与 F8 幂等边界：幂等=顺序重复调用结果一致性(任意 ≥2 次重复)；并发=同一并发
    窗口内调用结果一致性(时间接近/重叠)。F3 仅当调用落入并发窗口才判定；时间上
    远离的重复调用(如两个独立步骤)不会触发 F3(F8 可能触发)。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("concurrency") or result.get("concurrency") or {}
    if not isinstance(_decl, dict) or not _decl.get("expect_consistent"):
        # 未声明 expect_consistent → 零影响(不标、不记证据)
        result["concurrency_race"] = False
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
    try:
        _hint = max(int(_decl.get("concurrency_hint", 2) or 2), 2)
    except (TypeError, ValueError):
        _hint = 2
    try:
        _window_ms = float(_decl.get("window_ms", CONCURRENCY_DEFAULT_WINDOW_MS) or 0)
        _window_ms = _window_ms if _window_ms > 0 else CONCURRENCY_DEFAULT_WINDOW_MS
    except (TypeError, ValueError):
        _window_ms = CONCURRENCY_DEFAULT_WINDOW_MS
    _sig_fields = _decl.get("body_signature_fields") or []
    if isinstance(_sig_fields, str):
        _sig_fields = [_sig_fields]
    _dup = _decl.get("db_duplicate_check") or {}
    if not isinstance(_dup, dict):
        _dup = {}

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

    result["concurrency_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["concurrency_race"] = False
        result["concurrency_evidence"] = (
            "声明 concurrency(端点=%s 方法=%s) 但未观测到匹配 API 调用，无法判定并发"
            % (_target or "*", _mm or "*")
        )
        return result

    # 按「同一操作」(URL+method) 分组
    _groups: dict = {}
    for _c in _matched:
        _groups.setdefault(_op_group_key(_c), []).append(_c)

    _race_found = False
    _race_details: list = []
    _checked_groups = 0
    _window_found = False
    for _k, _g in _groups.items():
        _clusters = _op_concurrent_clusters(_g, _window_ms)
        if any(len(cl) >= 2 for cl in _clusters):
            _window_found = True
        _cc = [cl for cl in _clusters if len(cl) >= _hint]
        if not _cc:
            continue
        _checked_groups += 1
        for _cl in _cc:
            _r = _concurrency_compare_cluster(_cl, _sig_fields, _dup)
            if _r:
                _race_found = True
                _race_details.append(
                    "%s %s — %s"
                    % (
                        str(_cl[0].get("method", "") or "?").upper(),
                        str(_cl[0].get("url", "") or "?")[:120],
                        "; ".join(_r),
                    )
                )

    result["concurrency_checked"] = bool(_checked_groups)
    if not _race_found:
        result["concurrency_race"] = False
        if not _window_found:
            result["concurrency_evidence"] = (
                "未观测到并发窗口(同一操作调用时间未接近/重叠，或仅单次，"
                "window_ms=%dms)，无法判定并发竞态" % int(_window_ms)
            )
        elif not _checked_groups:
            result["concurrency_evidence"] = (
                "观测到并发窗口但调用数不足 concurrency_hint=%d(实际窗口内调用不足)，"
                "不判定" % _hint
            )
        else:
            result["concurrency_evidence"] = (
                "并发正常: %d 个并发窗口(≥%d次调用)结果一致(状态码+ok语义%s)"
                % (_checked_groups, _hint, "+body签名" if _sig_fields else "")
            )
        return result

    result["concurrency_race"] = True
    _detail = "; ".join(_race_details[:5])
    result["concurrency_evidence"] = (
        "并发竞态: 同一并发窗口内调用结果不一致/暴露竞态 — %s" % _detail
    )
    _api_evidence = _api.get("evidence", "") or ""
    if CONCURRENCY_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + CONCURRENCY_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if CONCURRENCY_TAG not in _summary:
        result["summary"] = (_summary + " " + CONCURRENCY_TAG).strip()[:500]
    if _decl.get("fail_on_race"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 concurrency → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F3] 并发竞态 → 标注 %s | %s", CONCURRENCY_TAG, _detail
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# F9 网络异常/弱网检测：公共小工具
# ═══════════════════════════════════════════════════════════════════════════
def _network_call_status_observed(call: dict) -> bool:
    """调用是否显式记录了状态码（status 键存在；缺失 → fail-open 不判状态）。"""
    return isinstance(call, dict) and "status" in call


def _flag_network(result: dict, checks: dict | None = None) -> dict:
    """★ F9 [已复核-DeepSeek-V3 2026-08-16]：网络异常/弱网标记。

    Args:
        result: verify_step 汇总后的结果(含 api 轨)。
        checks: 本步 checks(可选)；声明位为 checks["network"]。
            省略时回落读取 result["network"]，故 _flag_network(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7/F8/F2/F3 保持同构)。

    声明形态 checks["network"]：
        {
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["POST"],                  # 方法过滤(可选)
            "expect_status": [200],               # 可选：期望状态码(不在 → [network_anomaly])
            "max_ms": 1000,                       # 可选：弱网慢响应阈值(超时 → [network_slow])
            "fail_on_network": false,             # opt-in：命中时把 passed 置 False
        }

    Returns:
        result(原地增强)：
          - 状态异常(命中端点调用状态码不在 expect_status)：result["network_anomaly"]=True，
            api 轨 evidence 与 summary 追加 [network_anomaly](含实际状态)；
          - 弱网慢(命中端点调用耗时 > max_ms)：result["network_slow"]=True，
            追加 [network_slow](含实测ms)；两者可同时命中。
          - 仅当声明 fail_on_network=True 才额外置 result["passed"]=False。
          - 未声明 / 无可判定断言 / 端点未观测到 / 无状态码或耗时数据 / 状态符合且
            耗时正常 → 不标(fail-open，不误杀)。

    与既有能力边界（互补不重复）：
      1. _verify_network.verify_network = 【check 级】network_check（url_pattern +
         expected 存在性/次数，fail-closed 声明即必须满足）；_flag_network 是
         【汇总层】fail-open 网络维度标记（异常状态码/弱网超时），两者互补。
      2. 与 F2 边界：F2 checks["performance"].max_ms = 纯性能慢（[perf_slow]）；
         F9 checks["network"].max_ms = 网络维度弱网/超时（[network_slow]），
         复用 _perf_elapsed_ms / _op_call_status helper，不重复计时/取状态。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("network") or result.get("network") or {}
    if not isinstance(_decl, dict):
        # 未声明 network → 零影响(不标、不记证据)
        result["network_anomaly"] = False
        result["network_slow"] = False
        return result
    _expect_raw = _decl.get("expect_status") or []
    _max_raw = _decl.get("max_ms")
    _has_max = _max_raw is not None
    if not _expect_raw and not _has_max:
        # 声明了 network 但无可判定断言(无 expect_status / max_ms) → 零影响
        result["network_anomaly"] = False
        result["network_slow"] = False
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

    _expect = set()
    if isinstance(_expect_raw, (list, tuple)):
        for _s in _expect_raw:
            try:
                _expect.add(int(_s))
            except (TypeError, ValueError):
                continue
    else:
        try:
            _expect.add(int(_expect_raw))
        except (TypeError, ValueError) as _e:
            _log.debug("_flag_network best-effort 容忍: %s", str(_e)[:120])
    try:
        _max_ms = float(_max_raw)
    except (TypeError, ValueError):
        _max_ms = None
        _has_max = False

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

    result["network_checked"] = False
    if not _matched:
        # 声明与观测不匹配端点 → 不标（inconclusive，不误杀）
        result["network_anomaly"] = False
        result["network_slow"] = False
        result["network_evidence"] = (
            "声明 network(端点=%s 方法=%s) 但未观测到匹配 API 调用，无法判定网络"
            % (_target or "*", _mm or "*")
        )
        return result

    # 可判定数据：状态码显式观测(供 anomaly) / 耗时数据(供 slow，复用 F2 数据源)
    _with_status = [c for c in _matched if _network_call_status_observed(c)]
    _with_time = [(c, _perf_elapsed_ms(c)) for c in _matched]
    _with_time = [(c, t) for c, t in _with_time if t is not None]
    _status_judgeable = bool(_expect) and bool(_with_status)
    _time_judgeable = _has_max and bool(_with_time)
    result["network_checked"] = _status_judgeable or _time_judgeable
    if not (_status_judgeable or _time_judgeable):
        # 命中调用无耗时数据(且无可判状态) → 降级不误杀
        result["network_anomaly"] = False
        result["network_slow"] = False
        result["network_evidence"] = (
            "声明 network(expect_status=%s max_ms=%s) 但命中调用无可用观测数据"
            "(状态码/耗时缺失)，无法判定"
            % (sorted(_expect) or "*", int(_max_ms) if _has_max else "*")
        )
        return result

    _anomalies: list = []
    if _expect:
        for _c in _with_status:
            _st = _op_call_status(_c)
            if _st not in _expect:
                _anomalies.append(
                    "%s %s → 实际状态 %d(期望%s)"
                    % (
                        str(_c.get("method", "") or "?").upper(),
                        str(_c.get("url", "") or "?")[:120],
                        _st,
                        sorted(_expect),
                    )
                )
    _slows: list = []
    if _has_max and _max_ms is not None:
        for _c, _t in _with_time:
            if _t > _max_ms:
                _slows.append(
                    "%s %s 实测 %.0fms 超过 max_ms=%d(弱网/超时)"
                    % (
                        str(_c.get("method", "") or "?").upper(),
                        str(_c.get("url", "") or "?")[:120],
                        _t,
                        int(_max_ms),
                    )
                )

    if not _anomalies and not _slows:
        # 状态符合且耗时正常 → 不标
        result["network_anomaly"] = False
        result["network_slow"] = False
        _ok_parts = []
        if _status_judgeable:
            _ok_parts.append("%d 个调用状态码均符合期望%s" % (len(_with_status), sorted(_expect)))
        if _time_judgeable:
            _ok_parts.append(
                "%d 个调用耗时均未超 max_ms=%d(最慢 %.0fms)"
                % (
                    len(_with_time),
                    int(_max_ms) if _max_ms is not None else 0,
                    max(t for _, t in _with_time),
                )
            )
        result["network_evidence"] = "网络正常: %s" % "; ".join(_ok_parts)
        return result

    _detail_parts = []
    if _anomalies:
        _detail_parts.append("状态异常 — %s" % "; ".join(_anomalies[:5]))
    if _slows:
        _detail_parts.append("弱网慢 — %s" % "; ".join(_slows[:5]))
    _detail = "; ".join(_detail_parts)

    result["network_anomaly"] = bool(_anomalies)
    result["network_slow"] = bool(_slows)
    result["network_evidence"] = "网络异常/弱网: %s" % _detail
    _api_evidence = _api.get("evidence", "") or ""
    _added = []
    if result["network_anomaly"] and NETWORK_ANOMALY_TAG not in _api_evidence:
        _added.append(NETWORK_ANOMALY_TAG)
    if result["network_slow"] and NETWORK_SLOW_TAG not in _api_evidence:
        _added.append(NETWORK_SLOW_TAG)
    if _added:
        _api["evidence"] = (
            _api_evidence + " " + " ".join(_added) + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    # summary 追加与 api evidence 相互独立（与 _flag_performance 同构）
    _summary = result.get("summary", "") or ""
    for _tag in _added:
        if _tag not in _summary:
            result["summary"] = (_summary + " " + _tag).strip()[:500]
            _summary = result["summary"]
    if _decl.get("fail_on_network"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 network → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F9] 网络异常/弱网 → 标注 %s | %s",
        " ".join(_added),
        _detail,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# F10 变异测试（mutation）：盲靶变异激活 → 双跑行为差异（behavioral delta）
# ═══════════════════════════════════════════════════════════════════════════
def _mutation_runs(result: dict) -> list:
    """取双跑观测 runs（约定键 result["mutation_runs"]）。

    结构：[{"state": "baseline", "key": None, "calls": [...]},
           {"state": "mutated", "key": "submit_broken", "calls": [...]}, ...]
    非法/缺失 → []（fail-open，不判）。
    """
    _runs = result.get("mutation_runs")
    if not isinstance(_runs, list):
        return []
    _out = []
    for _r in _runs:
        if not isinstance(_r, dict):
            continue
        _calls = _r.get("calls")
        if not isinstance(_calls, list):
            continue
        _out.append(
            {
                "state": str(_r.get("state", "") or "").strip().lower(),
                "key": _r.get("key"),
                "calls": [c for c in _calls if isinstance(c, dict)],
            }
        )
    return _out


def _mutation_body_sig_value(resp_body, field) -> tuple:
    """单字段 body 签名值。

    普通字段：_extract_json_path 取路径值（如 "[0].status"，与 F8/F3 同源）；
    长度字段："len:path"（path 为空 = 整个响应体）→ ("len", 长度)，用于
    「删除后行数应减少」这类计数语义（delete_broken 仅靠状态码/ok 不可见）。
    提取失败 → ("val", None) / ("len", None)；两 run 同为 None → 不误判。
    """
    _f = str(field).strip()
    if _f.startswith("len:"):
        _inner = _f[4:].strip()
        _val = _extract_json_path(resp_body, _inner) if _inner else resp_body
        # resp_body 常为 JSON 字符串：先解析再取长度（len: 语义 = 行数/元素数，
        # 而非字符数；解析失败回落原值长度）
        if isinstance(_val, (str, bytes)):
            try:
                _val = json.loads(_val)
            except (ValueError, TypeError) as _e:
                _log.debug("_mutation_body_sig_value best-effort 容忍: %s", str(_e)[:120])
        if isinstance(_val, (list, tuple, dict, str, bytes)):
            return ("len", len(_val))
        return ("len", None)
    return ("val", _extract_json_path(resp_body, _f))


def _mutation_call_signature(call: dict, sig_fields: list) -> tuple:
    """单次调用观测签名：(group_key, 状态码+ok 语义, [body 签名值...])。"""
    return (
        _op_group_key(call),
        _op_call_outcome(call),
        tuple(_mutation_body_sig_value(call.get("resp_body"), f) for f in sig_fields),
    )


def _mutation_run_signature(calls_matched: list, sig_fields: list) -> tuple:
    """一个 run 的观测签名：命中调用按序转签名。

    保序（驱动侧同一 case 双跑、端点序列一致）；调用数差异本身也是行为差异
    （如某调用仅在变异 run 出现/消失），由 tuple 长度不同体现。
    """
    return tuple(_mutation_call_signature(c, sig_fields) for c in calls_matched)


def _mutation_sig_str(sig: tuple) -> str:
    """观测签名人类可读：group 200/ok=True body=('val','已通过')。"""
    _gk, _out, _sig = sig
    _s = "%s %s/ok=%s" % (_gk, _out[0], _out[1])
    if _sig:
        _s += " body=%s" % (",".join(str(v) for v in _sig))
    return _s


def _mutation_diff_summary(base_sig: tuple, mut_sig: tuple) -> str:
    """两 run 观测签名差异摘要（首个差异调用；数量差异单独说明）。"""
    for _i, (_b, _m) in enumerate(zip(base_sig, mut_sig), start=1):
        if _b != _m:
            return "第%d个命中调用 baseline(%s) vs mutated(%s)" % (
                _i,
                _mutation_sig_str(_b),
                _mutation_sig_str(_m),
            )
    if len(base_sig) != len(mut_sig):
        return "命中调用数 baseline=%d vs mutated=%d" % (
            len(base_sig),
            len(mut_sig),
        )
    return "观测签名不一致"


def _mutation_filter_calls(calls: list, target: str, methods: list) -> list:
    """按声明端点/方法过滤调用（与 F8/F3/F9 同构；target/methods 均可选）。"""
    _out = []
    for _c in calls:
        if target and not _match_url_pattern(
            target, str(_c.get("url", "") or "")
        ):
            continue
        _m = str(_c.get("method", "") or "").upper()
        if methods and _m and _m not in methods:
            continue
        _out.append(_c)
    return _out


def _flag_mutation_scan(result: dict, checks: dict | None = None) -> dict:
    """★ F10 [已复核-DeepSeek-V3 2026-08-16]：变异测试（盲靶变异激活）标记。

    回答「缺陷激活后引擎是否感知」：驱动侧对同一 case 做 baseline（变异全关）
    与 mutated（逐 key 激活）双跑，把两次观测的同一端点调用合并进
    result["mutation_runs"]；本函数对每个声明的 key 比对两次观测的同一端点
    调用（状态码 + body ok 语义 [+ 可选 body 签名字段]）→ 有差异标
    [mutation_detected]（含 key 与差异摘要）。

    Args:
        result: verify_step 汇总后的结果(含 api 轨) + 双跑观测 result["mutation_runs"]。
        checks: 本步 checks(可选)；声明位为 checks["mutation_scan"]。
            省略时回落读取 result["mutation_scan"]，故 _flag_mutation_scan(result)
            单参调用同样可用(与 GAP-08/GAP-05/GAP-04/F7/F8/F2/F3/F9 保持同构)。

    声明形态 checks["mutation_scan"]：
        {
            "keys": ["submit_broken", "approve_noop", "reject_404", "delete_broken"],
                                                          # 要扫描的变异键(必填)
            "endpoint"/"url_pattern"/"target": URL pattern(可选，缺省全部),
            "methods": ["POST"],                          # 方法过滤(可选)
            "body_signature_fields": ["[0].status", "len:"],
                                                          # 可选：跨 run 必须一致的
                                                          # body 签名字段("len:path"=长度)
            "fail_on_mutation": false,                    # opt-in：命中时把 passed 置 False
        }

    双跑观测 result["mutation_runs"]（由驱动侧约定）：
        [
            {"state": "baseline", "key": None, "calls": [<API轨调用>...]},
            {"state": "mutated", "key": "<变异键>", "calls": [<API轨调用>...]},
            ...
        ]

    Returns:
        result(原地增强)：
          - 命中(某 key 的 baseline vs mutated 同一端点调用观测签名不一致)：
            result["mutation_detected"]=True，api 轨 evidence 与 summary 追加
            [mutation_detected]，result["mutation_evidence"]/["mutation_details"]
            记录证据明细；仅当声明 fail_on_mutation=True 才额外置
            result["passed"]=False。
          - 一致/未声明/无双跑数据/该 key 未跑 mutated/端点未观测到：
            mutation_detected=False，原样返回(fail-open，不误杀)。

    与既有能力边界（不重复造轮子）：
      1. F8 幂等 = 同一实现下顺序重复调用结果一致性；F10 = 同一 case 在
         变异开/关两种实现下的行为差异（behavioral delta），复用 F8
         _op_call_outcome/_op_group_key/_op_call_status，不重复取状态/分组。
      2. 引擎无变异知识（不知道盲靶哪个 key 被激活），只比对驱动侧注入的
         双跑观测；未声明/无双跑数据 → 零影响。
    """
    if not isinstance(result, dict):
        return result
    _decl = (checks or {}).get("mutation_scan") or result.get("mutation_scan") or {}
    if not isinstance(_decl, dict):
        # 未声明 mutation_scan → 零影响(不标、不记证据)
        result["mutation_detected"] = False
        return result
    _keys = _decl.get("keys")
    if isinstance(_keys, str):
        _keys = [_keys]
    _keys = [str(k).strip() for k in (_keys or []) if str(k).strip()]
    if not _keys:
        # 声明了 mutation_scan 但无 keys → 零影响
        result["mutation_detected"] = False
        return result

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
    _sig_fields = _decl.get("body_signature_fields") or []
    if isinstance(_sig_fields, str):
        _sig_fields = [_sig_fields]

    _runs = _mutation_runs(result)
    _baseline = None
    for _r in _runs:
        if _r["state"] == "baseline":
            _baseline = _r
            break
    result["mutation_checked"] = False
    if _baseline is None or not _baseline["calls"]:
        # 无双跑数据(baseline 缺失) → fail-open(不误杀)
        result["mutation_detected"] = False
        result["mutation_evidence"] = (
            "声明 mutation_scan(keys=%s) 但未观测到 baseline 双跑数据"
            "(result.mutation_runs 缺失/为空)，无法判定变异感知" % _keys
        )
        return result

    _base_calls = _mutation_filter_calls(_baseline["calls"], _target, _mm)
    if not _base_calls:
        # baseline 命中端点未观测到 → fail-open(不误杀)
        result["mutation_detected"] = False
        result["mutation_evidence"] = (
            "声明 mutation_scan(端点=%s 方法=%s) 但 baseline 未观测到匹配 API 调用，"
            "无法判定变异感知" % (_target or "*", _mm or "*")
        )
        return result
    _base_sig = _mutation_run_signature(_base_calls, _sig_fields)

    _detected: list = []
    _details: list = []
    for _k in _keys:
        _mut = None
        for _r in _runs:
            if _r["state"] == "mutated" and str(_r.get("key") or "") == _k:
                _mut = _r
                break
        if _mut is None or not _mut["calls"]:
            continue  # 该 key 未跑 mutated run → fail-open 跳过
        _mut_calls = _mutation_filter_calls(_mut["calls"], _target, _mm)
        if not _mut_calls:
            continue  # mutated 命中端点未观测到 → inconclusive 跳过
        result["mutation_checked"] = True
        _mut_sig = _mutation_run_signature(_mut_calls, _sig_fields)
        if _base_sig == _mut_sig:
            continue
        _diff = _mutation_diff_summary(_base_sig, _mut_sig)
        _detected.append(_k)
        _details.append(
            {
                "key": _k,
                "diff": _diff,
                "baseline_calls": len(_base_calls),
                "mutated_calls": len(_mut_calls),
                "signature_fields": list(_sig_fields),
            }
        )

    if not _detected:
        result["mutation_detected"] = False
        if result["mutation_checked"]:
            result["mutation_evidence"] = (
                "变异未感知: %d 个 key 的 baseline vs mutated 双跑观测一致"
                "(状态码+ok语义%s)" % (len(_keys), "+body签名" if _sig_fields else "")
            )
        else:
            result["mutation_evidence"] = (
                "声明 mutation_scan(keys=%s) 但无一 key 完成双跑比对"
                "(缺 baseline/mutated 命中调用)，无法判定变异感知" % _keys
            )
        return result

    result["mutation_detected"] = True
    result["mutation_details"] = _details
    _detail = "; ".join(
        "key=%s %s" % (d["key"], d["diff"]) for d in _details
    )
    result["mutation_evidence"] = (
        "变异被感知(behavioral delta): %s" % _detail
    )
    _api = result.get("api") or {}
    _api_evidence = _api.get("evidence", "") or ""
    if MUTATION_DETECTED_TAG not in _api_evidence:
        _api["evidence"] = (
            _api_evidence + " " + MUTATION_DETECTED_TAG + " " + _detail
        ).strip()[:500]
        result["api"] = _api
    _summary = result.get("summary", "") or ""
    if MUTATION_DETECTED_TAG not in _summary:
        result["summary"] = (_summary + " " + MUTATION_DETECTED_TAG).strip()[:500]
    if _decl.get("fail_on_mutation"):
        # opt-in 升级为硬失败(默认关闭；存量用例未声明 mutation_scan → 永不触发)
        result["passed"] = False
    _log.warning(
        "[F10] 变异被感知 → 标注 %s | %s", MUTATION_DETECTED_TAG, _detail
    )
    return result


__all__ = [
    "_flag_idempotency",
    "_op_call_outcome",
    "_op_duplicate_hit",
    "_flag_performance",
    "_perf_elapsed_ms",
    # ★ F3 (2026-08-16): 并发竞态检测（复用 F8 helper）
    "_flag_concurrency",
    "_op_call_ts",
    "_op_call_end_ts",
    "_op_concurrent_clusters",
    # ★ F9 (2026-08-16): 网络异常/弱网检测（复用 F2 _perf_elapsed_ms / F8 _op_call_status）
    "_flag_network",
    "_network_call_status_observed",
    # ★ F10 (2026-08-16): 变异测试（双跑行为差异检测）
    "_flag_mutation_scan",
    "_mutation_runs",
    "_mutation_run_signature",
    "IDEMPOTENCY_TAG",
    "PERF_SLOW_TAG",
    "CONCURRENCY_TAG",
    "NETWORK_ANOMALY_TAG",
    "NETWORK_SLOW_TAG",
    "MUTATION_DETECTED_TAG",
]
