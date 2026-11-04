# -*- coding: utf-8 -*-
"""性能门禁规则加载 — ``perf_rules.yaml`` 深合并（唯一配置源）。

**不写死靶子**：代码只认 metric key，不认靶子名。要新增/调整靶子，
只需改 ``perf_rules.yaml`` 的 ``targets.<name>``，无需改任何一行 Python。

合并语义（§8.4）：``defaults`` 与 ``targets.<name>`` **递归深合并**，
叶子值以 target 为准；``metrics: []`` 表示该组关停。

对外契约：``load_rules(target, path=None) -> dict`` **绝不抛异常**，
加载失败时回落内置 ``_defaults()`` 并打 warning。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from typing import Any

from src.extensions.perf_baseline.schemas import KIND_API, KIND_WEB

_log = logging.getLogger(__name__)

#: 默认规则文件路径（与本模块同目录）
DEFAULT_RULES_PATH = os.path.join(os.path.dirname(__file__), "perf_rules.yaml")

# ── 原始 YAML 解析缓存：{abs_path: (mtime, raw_dict)}，避免每样本重复读盘 ──
_raw_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.RLock()


def _builtin_defaults() -> dict[str, Any]:
    """内置兜底规则（YAML 缺失 / 解析失败时使用）。

    与 ``perf_rules.yaml`` 的 ``defaults`` 段保持同构，是最后一道安全网。
    """
    return {
        "version": 1,
        "enabled": True,
        KIND_WEB: {
            "metrics": [
                "ttfb_ms",
                "dcl_ms",
                "load_ms",
                "resource_count",
                "resource_bytes",
                "longtask_ms",
                "cls_approx",
            ],
            "thresholds": {
                "ttfb_ms": 800,
                "dcl_ms": 2500,
                "load_ms": 5000,
                "resource_count": 150,
                "resource_bytes": 3145728,
                "longtask_ms": 300,
                "cls_approx": 0.25,
            },
        },
        KIND_API: {
            "metrics": ["elapsed_ms", "status_code", "payload_bytes"],
            "thresholds": {
                "elapsed_ms": 1500,
                "status_code": 499,  # ≥500 判 FAIL（与 perf_rules.yaml defaults 对齐，最后一道安全网）
                "payload_bytes": 1048576,
            },
        },
        "relative": {
            "pre_gate_pct": 80,
            "fail_pct": 100,
            "noise_floor": {
                "ttfb_ms": 50,
                "dcl_ms": 100,
                "load_ms": 150,
                "longtask_ms": 100,
                "cls_approx": 0.05,
                "resource_bytes": 262144,
                "elapsed_ms": 50,
                "payload_bytes": 65536,
            },
        },
    }


# 对外别名（类图 RulesLoader._defaults()）
_defaults = _builtin_defaults


def _deep_merge(base: dict, override: dict) -> dict:
    """递归深合并：dict 递归、其余类型（含 list）整体覆盖。

    ``metrics: []`` 因此可以整体覆盖 defaults 的非空列表 → 关停该组。
    """
    if not isinstance(base, dict):
        return copy.deepcopy(override) if isinstance(override, dict) else override
    merged = copy.deepcopy(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_raw(path: str) -> dict[str, Any]:
    """读取并缓存原始 YAML。任何异常都吞掉并返回空 dict。"""
    abs_path = os.path.abspath(path)
    try:
        mtime = os.path.getmtime(abs_path)
    except OSError:
        _log.warning("[perf_gate] 规则文件不存在，回落内置 defaults: %s", abs_path)
        return {}

    with _cache_lock:
        cached = _raw_cache.get(abs_path)
        if cached and cached[0] == mtime:
            return cached[1]

    raw: dict[str, Any] = {}
    try:
        import yaml  # 延迟导入：PyYAML 已在 requirements，但保持模块可独立导入

        with open(abs_path, "r", encoding="utf-8") as fp:
            parsed = yaml.safe_load(fp)
        if isinstance(parsed, dict):
            raw = parsed
        else:
            _log.warning("[perf_gate] 规则文件顶层非 dict，忽略: %s", abs_path)
    except Exception as exc:  # noqa: BLE001 — 对外不 raise
        _log.warning("[perf_gate] 规则文件解析失败(%s)，回落内置 defaults", str(exc)[:120])
        raw = {}

    with _cache_lock:
        _raw_cache[abs_path] = (mtime, raw)
    return raw


def load_rules(target: str, path: str | None = None) -> dict[str, Any]:
    """加载并深合并指定靶子的性能规则。**绝不抛异常**。

    参数：
        target: 靶子名（如 ``"smartshop"``）。未在 yaml 中声明时只用 defaults。
        path: 规则文件路径，缺省用同目录 ``perf_rules.yaml``。

    返回：
        已深合并的规则 dict，至少包含 ``web`` / ``api`` / ``relative`` 三段。
    """
    target_name = (target or "").strip()
    rules_path = path or DEFAULT_RULES_PATH

    raw = _load_raw(rules_path)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else None
    merged = _deep_merge(_builtin_defaults(), defaults or {})
    if not defaults:
        _log.debug("[perf_gate] 使用内置 defaults（yaml 无 defaults 段）")

    targets = raw.get("targets")
    if target_name and isinstance(targets, dict):
        override = targets.get(target_name)
        if isinstance(override, dict):
            merged = _deep_merge(merged, override)
        else:
            _log.debug("[perf_gate] 靶子 %s 无专属规则，沿用 defaults", target_name)

    if isinstance(raw.get("version"), int):
        merged["version"] = raw["version"]
    merged["target"] = target_name
    return _sanitize(merged)


def _sanitize(rules: dict[str, Any]) -> dict[str, Any]:
    """结构兜底：保证 web/api/relative 三段结构完整、类型正确。

    阈值表会被**裁剪到 metrics 列表**内：内置 defaults 作为合并 base，会把
    自定义规则文件未声明的指标阈值带进来；这些阈值对判定无效（gate 只遍历
    metrics 列表），留着只会污染报告与调试输出。裁剪后 ``metrics: []``
    的组自然得到空阈值表，语义与"整组关停"一致。

    注意：裁剪**不削弱** fail-closed —— 若用户声明了某指标却漏配阈值，
    内置 defaults 的阈值仍会通过深合并补齐。
    """
    safe = dict(rules)
    for kind in (KIND_WEB, KIND_API):
        group = safe.get(kind)
        if not isinstance(group, dict):
            group = {}
        metrics = group.get("metrics")
        metric_names = [str(m) for m in metrics] if isinstance(metrics, list) else []
        group["metrics"] = metric_names
        thresholds = group.get("thresholds")
        if not isinstance(thresholds, dict):
            thresholds = {}
        enabled = set(metric_names)
        group["thresholds"] = {
            str(name): value for name, value in thresholds.items() if str(name) in enabled
        }
        safe[kind] = group

    relative = safe.get("relative")
    if not isinstance(relative, dict):
        relative = {}
    try:
        pre_gate = float(relative.get("pre_gate_pct", 80))
    except (TypeError, ValueError):
        pre_gate = 80.0
    try:
        fail = float(relative.get("fail_pct", 100))
    except (TypeError, ValueError):
        fail = 100.0
    # pre_gate 必须小于等于 fail，否则 PRE_GATE 区间为空（配置错误兜底）
    if pre_gate > fail:
        _log.warning(
            "[perf_gate] relative 配置非法(pre_gate_pct=%s > fail_pct=%s)，已对调", pre_gate, fail
        )
        pre_gate, fail = fail, pre_gate
    safe["relative"] = {
        "pre_gate_pct": pre_gate,
        "fail_pct": fail,
        # 噪声地板与相对阈值同属「相对判定」这一段配置，必须随段落一起保留
        "noise_floor": relative.get("noise_floor")
        if isinstance(relative.get("noise_floor"), dict)
        else {},
    }
    safe["enabled"] = bool(safe.get("enabled", True))
    return safe


def metrics_of(rules: dict[str, Any], kind: str) -> list[str]:
    """取某类（web/api）启用的指标列表。空列表 = 该组关停。"""
    group = (rules or {}).get(kind)
    if not isinstance(group, dict):
        return []
    metrics = group.get("metrics")
    return [str(m) for m in metrics] if isinstance(metrics, list) else []


def thresholds_of(rules: dict[str, Any], kind: str) -> dict[str, float]:
    """取某类（web/api）的绝对阈值表。缺失指标 = 不做绝对校验。"""
    group = (rules or {}).get(kind)
    if not isinstance(group, dict):
        return {}
    thresholds = group.get("thresholds")
    if not isinstance(thresholds, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in thresholds.items():
        try:
            result[str(name)] = float(value)
        except (TypeError, ValueError):
            _log.debug("[perf_gate] 忽略非法阈值 %s=%r", name, value)
    return result


def relative_of(rules: dict[str, Any]) -> tuple[float, float]:
    """取相对判定区间 ``(pre_gate_pct, fail_pct)``。"""
    relative = (rules or {}).get("relative")
    if not isinstance(relative, dict):
        return 80.0, 100.0
    try:
        return float(relative.get("pre_gate_pct", 80)), float(relative.get("fail_pct", 100))
    except (TypeError, ValueError):
        return 80.0, 100.0


def noise_floor_of(rules: dict[str, Any]) -> dict[str, float]:
    """取相对判定的**绝对噪声地板**表（指标 → 最小可判退化增量）。

    未列出的指标 → 0.0（纯百分比判定，与历史行为一致）。
    """
    relative = (rules or {}).get("relative")
    floor = relative.get("noise_floor") if isinstance(relative, dict) else None
    if not isinstance(floor, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in floor.items():
        try:
            result[str(name)] = float(value)
        except (TypeError, ValueError):
            _log.debug("[perf_gate] 忽略非法噪声地板 %s=%r", name, value)
    return result


def clear_cache() -> None:
    """清空 YAML 解析缓存（供单测与热重载使用）。"""
    with _cache_lock:
        _raw_cache.clear()


__all__ = [
    "DEFAULT_RULES_PATH",
    "load_rules",
    "metrics_of",
    "thresholds_of",
    "relative_of",
    "noise_floor_of",
    "clear_cache",
]
